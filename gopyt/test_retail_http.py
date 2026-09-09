"""No network dataset dependency: real HTTP authorization and atomicity fixture."""
import unittest
from tools.retail_http_probe import Server,boundaries


class RetailHTTP(unittest.TestCase):
    def test_authenticated_typed_atomic_batch_boundary(self):
        with Server() as server:
            server.start()
            self.assertEqual(len(boundaries(server)),4)
            server.stop(abrupt=True);server.start()
            self.assertEqual(server.request('/read',{'keys':['retail/race/a','retail/race/b']})[1],
                             {'outcome':'ok','values':['1','1']})


if __name__=='__main__':unittest.main()
