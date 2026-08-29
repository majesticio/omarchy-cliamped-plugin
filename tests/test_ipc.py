import os
import unittest
from unittest.mock import MagicMock, patch

import cliamp_ipc


class IPCClientTests(unittest.TestCase):
    @patch("cliamp_ipc.socket.socket")
    def test_sends_and_receives_one_newline_framed_json_message(self, socket_factory):
        client = MagicMock()
        client.recv.return_value = b'{"ok":true,"state":"playing"}\n'
        socket_factory.return_value = client

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/test-config"}):
            response = cliamp_ipc.send_request({"cmd": "status"})

        client.connect.assert_called_once_with("/test-config/cliamp/cliamp.sock")
        client.sendall.assert_called_once_with(b'{"cmd":"status"}\n')
        client.close.assert_called_once_with()
        self.assertEqual(response, {"ok": True, "state": "playing"})


if __name__ == "__main__":
    unittest.main()
