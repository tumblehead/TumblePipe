"""Houdini panel for managing RPC server.

Provides a UI panel for starting, stopping, and monitoring the RPC server
within Houdini, including connection information and command monitoring.
"""

import hou
from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QGroupBox,
    QFormLayout,
    QSpinBox,
    QCheckBox,
    QScrollArea,
    QSizePolicy,
)

from ..rpc.houdini_server import (
    start_server,
    stop_server,
    get_server,
    is_server_running,
    get_connection_info,
)


class RpcServerPanel(QWidget):
    """Panel widget for RPC server management."""

    def __init__(self, parent=None):
        super().__init__(parent)

        # State
        self._auto_start = True
        self._auto_start_port = None
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._update_status)
        self._update_timer.start(1000)

        self._init_ui()
        self._update_status()

        # Auto-start if enabled
        if self._auto_start and not is_server_running():
            self._start_server()

    def _init_ui(self):
        """Initialize the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # Server Control Section
        control_group = QGroupBox("RPC Server Control")
        control_layout = QVBoxLayout(control_group)

        # Status display
        status_layout = QFormLayout()
        self._status_label = QLabel("Stopped")
        self._host_label = QLabel("-")
        self._port_label = QLabel("-")
        self._commands_label = QLabel("-")

        status_layout.addRow("Status:", self._status_label)
        status_layout.addRow("Host:", self._host_label)
        status_layout.addRow("Port:", self._port_label)
        status_layout.addRow("Commands:", self._commands_label)

        control_layout.addLayout(status_layout)

        # Control buttons
        button_layout = QHBoxLayout()
        self._start_button = QPushButton("Start Server")
        self._start_button.clicked.connect(self._start_server)
        self._stop_button = QPushButton("Stop Server")
        self._stop_button.clicked.connect(self._stop_server)
        self._stop_button.setEnabled(False)

        button_layout.addWidget(self._start_button)
        button_layout.addWidget(self._stop_button)
        button_layout.addStretch()

        control_layout.addLayout(button_layout)

        # Configuration section
        config_group = QGroupBox("Configuration")
        config_layout = QFormLayout(config_group)

        self._auto_start_check = QCheckBox()
        self._auto_start_check.setChecked(self._auto_start)
        self._auto_start_check.toggled.connect(self._on_auto_start_changed)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(8000, 65535)
        self._port_spin.setValue(8543)

        config_layout.addRow("Auto-start:", self._auto_start_check)
        config_layout.addRow("Preferred Port:", self._port_spin)

        # Connection Info Section
        info_group = QGroupBox("Connection Information")
        info_layout = QVBoxLayout(info_group)

        self._connection_text = QTextEdit()
        self._connection_text.setReadOnly(True)
        self._connection_text.setMaximumHeight(100)
        self._connection_text.setFont(hou.qt.createFont("courier", 9))

        info_layout.addWidget(self._connection_text)

        # Copy button
        copy_layout = QHBoxLayout()
        self._copy_button = QPushButton("Copy Connection Info")
        self._copy_button.clicked.connect(self._copy_connection_info)
        self._copy_button.setEnabled(False)
        copy_layout.addWidget(self._copy_button)
        copy_layout.addStretch()
        info_layout.addLayout(copy_layout)

        # Usage Examples Section
        examples_group = QGroupBox("Usage Examples")
        examples_layout = QVBoxLayout(examples_group)

        examples_text = QTextEdit()
        examples_text.setReadOnly(True)
        examples_text.setMaximumHeight(150)
        examples_text.setFont(hou.qt.createFont("courier", 9))

        examples_content = """# Ping server
uv run python -m houdini_client.cli ping --port {port}

# Execute command
uv run python -m houdini_client.cli exec system.time --port {port}

# Create node
uv run python -m houdini_client.cli exec node.create \\
  --params '{{"parent_path": "/obj", "node_type": "geo"}}' --port {port}

# Save scene
uv run python -m houdini_client.cli exec scene.save --port {port}"""

        examples_text.setPlainText(examples_content)
        examples_layout.addWidget(examples_text)

        # Add all sections to main layout
        layout.addWidget(control_group)
        layout.addWidget(config_group)
        layout.addWidget(info_group)
        layout.addWidget(examples_group)
        layout.addStretch()

        # Store references for updates
        self._examples_text = examples_text

    def _start_server(self):
        """Start the RPC server."""
        try:
            preferred_port = (
                self._port_spin.value()
                if self._port_spin.value() != 8543
                else None
            )
            server = start_server("localhost", preferred_port)
            self._update_status()
            hou.ui.setStatusMessage(
                f"RPC server started on port {server.port}",
                severity=hou.severityType.Message,
            )
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to start RPC server: {e}",
                severity=hou.severityType.Error,
            )

    def _stop_server(self):
        """Stop the RPC server."""
        try:
            stop_server()
            self._update_status()
            hou.ui.setStatusMessage(
                "RPC server stopped", severity=hou.severityType.Message
            )
        except Exception as e:
            hou.ui.displayMessage(
                f"Failed to stop RPC server: {e}",
                severity=hou.severityType.Error,
            )

    def _update_status(self):
        """Update the status display."""
        if is_server_running():
            server = get_server()
            info = get_connection_info()

            self._status_label.setText("Running")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
            self._host_label.setText(str(server.host))
            self._port_label.setText(str(server.port))
            self._commands_label.setText(
                str(len(info["commands"])) if info else "?"
            )

            self._start_button.setEnabled(False)
            self._stop_button.setEnabled(True)
            self._copy_button.setEnabled(True)

            # Update connection info
            if info:
                connection_info = f"""Host: {info["host"]}
Port: {info["port"]}
Status: {"Running" if info["running"] else "Stopped"}
Available Commands: {len(info["commands"])}

Client Connection:
uv run python -m houdini_client.cli ping --host {info["host"]} --port {info["port"]}"""
                self._connection_text.setPlainText(connection_info)

                # Update examples with actual port
                examples_content = self._examples_text.toPlainText()
                updated_examples = examples_content.format(port=info["port"])
                self._examples_text.setPlainText(updated_examples)
        else:
            self._status_label.setText("Stopped")
            self._status_label.setStyleSheet("color: red; font-weight: bold;")
            self._host_label.setText("-")
            self._port_label.setText("-")
            self._commands_label.setText("-")

            self._start_button.setEnabled(True)
            self._stop_button.setEnabled(False)
            self._copy_button.setEnabled(False)

            self._connection_text.setPlainText("Server not running")

    def _copy_connection_info(self):
        """Copy connection information to clipboard."""
        if is_server_running():
            info = get_connection_info()
            if info:
                clipboard_text = f"--host {info['host']} --port {info['port']}"
                hou.ui.copyTextToClipboard(clipboard_text)
                hou.ui.setStatusMessage(
                    "Connection info copied to clipboard",
                    severity=hou.severityType.Message,
                )

    def _on_auto_start_changed(self, checked):
        """Handle auto-start checkbox change."""
        self._auto_start = checked

    def closeEvent(self, event):
        """Handle widget close event."""
        self._update_timer.stop()
        super().closeEvent(event)


def create_rpc_panel():
    """Create and return the RPC server panel widget.

    This function is called by Houdini when creating the panel.
    """
    return RpcServerPanel()


# Register the panel with Houdini
def register_panel():
    """Register the RPC panel with Houdini's panel system."""
    panel_interface = hou.pypanel.interfacesInPane(hou.paneTabType.PythonPanel)

    if "rpc_server" not in [interface.name() for interface in panel_interface]:
        hou.pypanel.installFile("rpc_server.pypanel")
