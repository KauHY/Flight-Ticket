import errno
import io
import unittest
import threading
from contextlib import redirect_stdout
from unittest.mock import patch
import app
import runtime

class StartupTests(unittest.TestCase):
    def test_duplicate_instance_is_successful_noop(self):
        output=io.StringIO()
        with patch.object(runtime,'LocalServer',side_effect=OSError(errno.EADDRINUSE,'in use')),patch.object(runtime,'is_flight_service',return_value=True),redirect_stdout(output),patch.object(app,'init') as init:
            self.assertEqual(app.main(),0)
            init.assert_not_called()
        self.assertIn('已在运行',output.getvalue())
    def test_other_service_is_not_silently_reused(self):
        with patch.object(runtime,'LocalServer',side_effect=OSError(errno.EADDRINUSE,'in use')),patch.object(runtime,'is_flight_service',return_value=False):
            with self.assertRaisesRegex(ValueError,'已被占用'):runtime.bind_server(app.Handler,8765)
    def test_non_conflict_error_not_reported_as_running(self):
        with patch.object(runtime,'LocalServer',side_effect=OSError(errno.EACCES,'access denied')):
            with self.assertRaises(OSError):runtime.bind_server(app.Handler,8765)
    def test_invalid_port_readable(self):
        with patch.dict('os.environ',{'PORT':'70000'}),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(app.main(),1)
        self.assertIn('PORT',output.getvalue())
    def test_live_exclusive_server_detects_duplicate(self):
        server=runtime.LocalServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with redirect_stdout(io.StringIO()) as output:
                self.assertIsNone(runtime.bind_server(app.Handler,server.server_port))
            self.assertIn('已在运行',output.getvalue())
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':unittest.main()
