"""
Business logic for ConciliacionComisionesDaFn.

Recibe archivos Excel de ventas y pagos, ejecuta la conciliación de comisiones,
y retorna un reporte Excel con el resultado.
"""

import json
import logging
import os
import re
import shutil
from typing import Dict, Any
import requests

from chask_foundation.backend.models import OrchestrationEvent
from api.orchestrator_requests import orchestrator_api_manager
from api.files_requests import files_api_manager

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Import reconciliation modules (relative to src/)
from main import run, ConciliationConfig


class FunctionBackend:
    """Backend for ConciliacionComisionesDaFn."""

    def __init__(self, orchestration_event: OrchestrationEvent):
        self.orchestration_event = orchestration_event
        logger.info(f"Initialized for org: {orchestration_event.organization.organization_id}")

    def process_request(self) -> str:
        """
        Download Excel files, run reconciliation, upload result.

        Returns:
            String result with download URL and summary stats
        """
        tool_args = self._extract_tool_args()

        test_mode = tool_args.get("test_mode", False)
        if test_mode:
            logger.info("[TEST MODE] Using bundled test files...")
            return self._run_test_mode(tool_args)

        file_uuids = tool_args.get("file_uuids", [])
        if not file_uuids:
            raise ValueError("Missing required parameter: file_uuids")

        fuzzy_threshold = int(tool_args.get("fuzzy_threshold", 85))
        tolerance = float(tool_args.get("tolerance", 1000))

        ventas_dir = "/tmp/ventas"
        pagos_dir = "/tmp/pagos"

        try:
            # Clean /tmp/ from previous invocations
            shutil.rmtree(ventas_dir, ignore_errors=True)
            shutil.rmtree(pagos_dir, ignore_errors=True)
            os.makedirs(ventas_dir, exist_ok=True)
            os.makedirs(pagos_dir, exist_ok=True)

            # Download files from session
            session_uuid = str(self.orchestration_event.session.session_id)
            self._download_files(session_uuid, file_uuids, ventas_dir, pagos_dir)

            # Find the pagos file
            pagos_files = os.listdir(pagos_dir)
            if not pagos_files:
                raise ValueError("No se encontró archivo de comisiones (COMISIONES*.xlsx)")
            pagos_file = os.path.join(pagos_dir, pagos_files[0])

            # Check for credit file
            credito_file = None
            for f in os.listdir(ventas_dir):
                if 'CREDITO' in f.upper():
                    credito_file = os.path.join(ventas_dir, f)
                    break

            # Run reconciliation
            config = ConciliationConfig(
                ventas_dir=ventas_dir,
                pagos_file=pagos_file,
                output_dir="/tmp",
                credito_file=credito_file,
                fuzzy_threshold=fuzzy_threshold,
                tolerance=tolerance
            )

            result = run(config)

            if result["status"] != "ok":
                raise Exception(result.get("error", "Reconciliation failed"))

            # Upload result Excel
            output_file = result["result"]["output_file"]
            file_url = self._upload_result(session_uuid, output_file)

            # Build summary
            summary = result["result"]["summary"]
            stats_msg = (
                f"Total vehículos: {summary['total_vehiculos']}, "
                f"Con bonos: {summary['vehiculos_con_bonos']}, "
                f"Con pago: {summary['vehiculos_con_pago']}, "
                f"Cobertura: {summary['pct_cobertura']}, "
                f"Diferencia: ${summary['monto_diferencia']:,.0f}"
            )

            return (
                f"Conciliación completada exitosamente.\n\n"
                f"{stats_msg}\n\n"
                f"Descarga el reporte: {file_url}"
            )

        finally:
            shutil.rmtree(ventas_dir, ignore_errors=True)
            shutil.rmtree(pagos_dir, ignore_errors=True)

    def _run_test_mode(self, tool_args: Dict[str, Any]) -> str:
        """Run reconciliation using bundled test files from src/test_data/."""
        fuzzy_threshold = int(tool_args.get("fuzzy_threshold", 85))
        tolerance = float(tool_args.get("tolerance", 1000))

        # Locate test data: Lambda deploys to /var/task/, local dev uses relative path
        task_root = os.environ.get("LAMBDA_TASK_ROOT", "")
        if task_root:
            test_data_dir = os.path.join(task_root, "test_data")
        else:
            test_data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "test_data")

        if not os.path.isdir(test_data_dir):
            raise FileNotFoundError(f"Test data directory not found: {test_data_dir}")

        ventas_dir = "/tmp/ventas"
        pagos_dir = "/tmp/pagos"

        try:
            shutil.rmtree(ventas_dir, ignore_errors=True)
            shutil.rmtree(pagos_dir, ignore_errors=True)
            os.makedirs(ventas_dir, exist_ok=True)
            os.makedirs(pagos_dir, exist_ok=True)

            # Classify bundled test files
            for fname in os.listdir(test_data_dir):
                if not fname.endswith(".xlsx"):
                    continue
                src_path = os.path.join(test_data_dir, fname)
                name_upper = fname.upper()
                if "COMISIONES" in name_upper:
                    dest = os.path.join(pagos_dir, fname)
                else:
                    dest = os.path.join(ventas_dir, fname)
                shutil.copy2(src_path, dest)
                logger.info(f"  [TEST] Copied: {fname} -> {dest}")

            # Find pagos file
            pagos_files = os.listdir(pagos_dir)
            if not pagos_files:
                raise ValueError("No se encontró archivo de comisiones en test_data/")
            pagos_file = os.path.join(pagos_dir, pagos_files[0])

            # Check for credit file
            credito_file = None
            for f in os.listdir(ventas_dir):
                if "CREDITO" in f.upper():
                    credito_file = os.path.join(ventas_dir, f)
                    break

            config = ConciliationConfig(
                ventas_dir=ventas_dir,
                pagos_file=pagos_file,
                output_dir="/tmp",
                credito_file=credito_file,
                fuzzy_threshold=fuzzy_threshold,
                tolerance=tolerance,
            )

            result = run(config)

            if result["status"] != "ok":
                raise Exception(result.get("error", "Reconciliation failed"))

            summary = result["result"]["summary"]
            stats_msg = (
                f"Total vehículos: {summary['total_vehiculos']}, "
                f"Con bonos: {summary['vehiculos_con_bonos']}, "
                f"Con pago: {summary['vehiculos_con_pago']}, "
                f"Cobertura: {summary['pct_cobertura']}, "
                f"Diferencia: ${summary['monto_diferencia']:,.0f}"
            )

            return (
                f"[TEST MODE - no upload] Conciliación completada exitosamente.\n\n"
                f"{stats_msg}"
            )

        finally:
            shutil.rmtree(ventas_dir, ignore_errors=True)
            shutil.rmtree(pagos_dir, ignore_errors=True)

    def _download_files(self, session_uuid: str, file_uuids: list,
                        ventas_dir: str, pagos_dir: str):
        """Download and classify files from the session."""
        response = orchestrator_api_manager.call(
            "get_all_files_for_session",
            session_uuid=session_uuid,
            access_token=self.orchestration_event.access_token,
            organization_id=self.orchestration_event.organization.organization_id,
        )

        attachments = response.get("files", [])
        selected = [f for f in attachments if f.get("file_uuid") in file_uuids]

        if not selected:
            raise ValueError(f"No files found for UUIDs: {file_uuids}")

        logger.info(f"Downloading {len(selected)} files...")

        for file_info in selected:
            file_url = file_info["file_url"]
            file_name = file_info["file_name"]

            resp = requests.get(file_url, timeout=60)
            resp.raise_for_status()

            # Classify by filename
            name_upper = file_name.upper()
            if 'COMISIONES' in name_upper:
                dest = os.path.join(pagos_dir, file_name)
            elif 'VENTAS' in name_upper or 'CREDITO' in name_upper:
                dest = os.path.join(ventas_dir, file_name)
            else:
                # Default to ventas
                dest = os.path.join(ventas_dir, file_name)

            with open(dest, "wb") as f:
                f.write(resp.content)
            logger.info(f"  Downloaded: {file_name} -> {dest}")

    def _upload_result(self, session_uuid: str, output_path: str) -> str:
        """Upload the result Excel file and return its URL."""
        file_name = os.path.basename(output_path)

        with open(output_path, "rb") as f:
            file_bytes = f.read()

        result = files_api_manager.call(
            "upload_file",
            session_uuid=session_uuid,
            file_name=file_name,
            file_content=file_bytes,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            access_token=self.orchestration_event.access_token,
            organization_id=self.orchestration_event.organization.organization_id,
        )

        if hasattr(result, "status_code"):
            if result.status_code != 200:
                raise ValueError(f"File upload failed: {result.status_code}")
            file_data = result.json()
        else:
            file_data = result

        return file_data["file_url"]

    def _extract_tool_args(self) -> Dict[str, Any]:
        """Extract tool call arguments from orchestration event."""
        extra_params = self.orchestration_event.extra_params or {}
        tool_calls = extra_params.get("tool_calls", [])
        if not tool_calls:
            logger.warning("No tool calls found")
            return {}
        return tool_calls[0].get("args", {})

    def _send_response(self, message: str, is_error: bool = False) -> bool:
        """Send response back to orchestrator via Kafka."""
        try:
            original_extra_params = self.orchestration_event.extra_params or {}
            tool_call_id = None
            tool_name = None
            if "tool_calls" in original_extra_params and original_extra_params["tool_calls"]:
                tool_call = original_extra_params["tool_calls"][0]
                tool_call_id = tool_call.get("id")
                tool_name = tool_call.get("name")

            extra_params = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "is_error": is_error
            }

            if original_extra_params.get("is_test"):
                extra_params["is_test"] = True
                if original_extra_params.get("test_execution_uuid"):
                    extra_params["test_execution_uuid"] = original_extra_params["test_execution_uuid"]

            evolve_response = orchestrator_api_manager.call(
                "evolve_event",
                parent_event_uuid=str(self.orchestration_event.event_id),
                event_type="function_call_response",
                source="agent",
                target="orchestrator",
                prompt=message,
                extra_params=extra_params,
                access_token=self.orchestration_event.access_token,
                organization_id=self.orchestration_event.organization.organization_id,
            )

            if evolve_response.get("status_code") not in (200, 201):
                raise Exception(f"Failed to evolve event: {evolve_response.get('error')}")

            evolved_uuid = evolve_response.get("uuid")
            if not evolved_uuid:
                raise Exception("Missing uuid in evolve response")

            response_event = self.orchestration_event.model_copy(deep=True)
            response_event.event_id = evolved_uuid
            response_event.event_type = "function_call_response"
            response_event.source = "agent"
            response_event.target = "orchestrator"
            response_event.prompt = message
            response_event.extra_params = evolve_response.get("extra_params", extra_params)

            orchestrator_api_manager.call(
                "forward_oe_to_kafka",
                orchestration_event=response_event.model_dump(),
                topic="orchestrator",
                access_token=response_event.access_token,
                organization_id=response_event.organization.organization_id,
            )

            logger.info(f"Response sent [{self.orchestration_event.event_id} -> {evolved_uuid}]")
            self.response_event_sent = True
            return True

        except Exception as e:
            logger.error(f"Failed to send response: {e}")
            return False

    def _extract_widget_params(self, param_names: list) -> Dict[str, Any]:
        """Extract widget parameters."""
        widget_data = self.orchestration_event.extra_params.get("widget_data", {})
        widgets = widget_data.get("widgets", [])
        widget_values = {w.get("name"): w.get("value") for w in widgets}
        result = {}
        for param_name in param_names:
            result[param_name] = widget_values.get(param_name) or widget_data.get(param_name)
        return result
