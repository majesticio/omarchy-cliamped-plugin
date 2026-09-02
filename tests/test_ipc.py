import unittest
from unittest.mock import patch

import cliamp_ipc


class IPCClientTests(unittest.TestCase):
    @patch("cliamp_ipc.send_requests")
    def test_single_request_api_delegates_to_the_authenticated_batch(self, send_requests):
        send_requests.return_value = [{"ok": True, "state": "playing", "session_mode": "tui"}]

        response = cliamp_ipc.send_request({"cmd": "status"})

        send_requests.assert_called_once_with(
            [{"cmd": "status"}],
            deadline_seconds=None,
            socket_path=None,
            trusted_executable=cliamp_ipc.TRUSTED_CLIAMP_PATH,
        )
        self.assertEqual(response, {"ok": True, "state": "playing", "session_mode": "tui"})


if __name__ == "__main__":
    unittest.main()
