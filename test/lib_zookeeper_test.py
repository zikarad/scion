# Copyright 2015 ETH Zurich
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
:mod:`lib_zookeeper_test` --- lib.zookeeper unit tests
======================================================
"""
# Stdlib
import logging
from functools import wraps
from unittest.mock import MagicMock, PropertyMock, call, patch

# External packages
import nose
import nose.tools as ntools
from kazoo.protocol.states import ZnodeStat

# SCION
import lib.zookeeper as libzk
from test.testcommon import MockCollection, SCIONTestError


def mock_wrapper(f):
    """
    Decorator to automate the patching/unpatching of the specific objects we
    need to mock for testing.
    """
    @wraps(f)
    def wrap(self, *args, **kwargs):
        if not hasattr(self, "mocks"):
            self.mocks = MockCollection()
        self.mocks.add('lib.zookeeper.KazooClient', 'kclient')
        self.mocks.add('lib.zookeeper.KazooRetry', 'kretry')
        self.mocks.add('kazoo.recipe.party.Party', 'kparty')
        self.mocks.add('kazoo.recipe.lock.Lock', 'klock')
        self.mocks.add('lib.zookeeper.threading.Thread', 'pythread')
        self.mocks.add('lib.zookeeper.threading.Event', 'pyevent')
        self.mocks.add('lib.zookeeper.threading.Semaphore', 'pysemaphore')
        self.mocks.add('lib.zookeeper.thread_safety_net', 'thread_safety_net')
        self.mocks.add('lib.zookeeper.kill_self', 'kill_self')
        self.mocks.start()
        self.mocks.kclient.return_value.mock_add_spec(
            ['Party', 'Lock', 'client_id'])
        self.mocks.kclient.return_value.Party = self.mocks.kparty
        self.mocks.kclient.return_value.Lock = self.mocks.klock
        try:
            return f(self, *args, **kwargs)
        finally:
            if hasattr(self, "mocks"):
                self.mocks.stop()
                del self.mocks
    return wrap


class BaseZookeeper(object):
    """
    Base class for lib.zookeeper.Zookeeper unit tests

    :cvar default_args:
    :type default_args:
    :cvar default_hosts:
    :type default_hosts:
    :cvar default_retry:
    :type default_retry:
    :ivar mocks:
    :type mocks:
    """
    default_args = [1, 2, "srvname", "srvid"]
    default_hosts = ["host1:9521", "host2:339"]
    default_retry = "asdfas"

    def _init_basic_setup(self, **kwargs):
        """
        Initiator.

        :param kwargs: keyword arguments and their values.
        :type kwargs: dictionary
        """
        self.mocks.kretry.return_value = self.default_retry
        all_args = self.default_args + [self.default_hosts]
        return libzk.Zookeeper(*all_args, **kwargs)


class TestZookeeperInit(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.__init__
    """

    @mock_wrapper
    def test_basic(self):
        """
        Test ZK's basic functionalities.
        """
        # Setup and call
        inst = self._init_basic_setup()
        # Tests
        self.mocks.kretry.assert_called_with(max_tries=-1, max_delay=1)
        ntools.eq_(inst._prefix, "/ISD%s-AD%s/%s" % (self.default_args[0],
                                                     self.default_args[1],
                                                     self.default_args[2]))
        self.mocks.kclient.assert_called_with(
            hosts=",".join(self.default_hosts), timeout=1.0,
            connection_retry=self.default_retry,
            logger=logging.getLogger("KazooClient"))
        ntools.assert_false(inst._connected.called)
        ntools.eq_(inst._parties, {})
        ntools.assert_false(inst._lock.called)
        ntools.eq_(inst._zk_lock, None)
        self.mocks.pysemaphore.assert_called_with(value=0)
        ntools.assert_false(inst._state_event.called)
        self.mocks.pythread.assert_called_with(
            target=self.mocks.thread_safety_net, args=(inst._state_handler,),
            name="libZK._state_handler", daemon=True)
        inst._zk.add_listener.assert_called_with(inst._state_listener)
        inst._zk.start.assert_called_with()

    @mock_wrapper
    def test_timeout(self):
        """
        Test ZK timeout.
        """
        # Setup and call
        self._init_basic_setup(timeout=4.5)
        # Tests
        self.mocks.kclient.assert_called_with(
            hosts=",".join(self.default_hosts), timeout=4.5,
            connection_retry=self.default_retry,
            logger=logging.getLogger("KazooClient"))

    @mock_wrapper
    def test_timeout_error(self):
        """
        Test timeout error.
        """
        # Raise a TimeoutError when self._zk.start() is called in __init__
        self.mocks.kclient.return_value.start.side_effect = \
            libzk.KazooTimeoutError
        # Setup and call
        inst = self._init_basic_setup()
        # Tests
        inst._zk.start.assert_called_with()
        self.mocks.kill_self.assert_called_with()


class TestZookeeperStateListener(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper._state_listener
    """

    @mock_wrapper
    def test(self):
        """
        Test basic functionalities.
        """
        # Setup and call
        inst = self._init_basic_setup()
        # Call, and make sure it returns False
        ntools.eq_(inst._state_listener("statist"), False)
        # Tests
        inst._state_event.release.assert_called_with()


class TestZookeeperStateHandler(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper._state_handler
    """

    def _setup(self, initial_state, new_state):
        """
        Initialize the ZK state.

        :param initial_state:
        :type initial_state:
        :param new_state:
        :type new_state:
        """
        inst = self._init_basic_setup()
        # Setup inst._state_event to allow a single iteration of the loop
        inst._state_event.acquire = MagicMock(spec_set=[], side_effect=[0])
        # Make inst._zk.state a PropertyMock, so we can check that it is read
        mock_state = PropertyMock(spec_set=[], return_value=new_state)
        # Required to attach a property to a mock:
        # http://www.voidspace.org.uk/python/mock/mock.html#mock.PropertyMock
        type(inst._zk).state = mock_state
        # Mock out the state change handlers
        inst._state_connected = MagicMock(spec_set=[])
        inst._state_suspended = MagicMock(spec_set=[])
        inst._state_lost = MagicMock(spec_set=[])
        return inst

    @mock_wrapper
    def _check(self, old_state, new_state):
        """
        Compare ZK states.

        :param old_state:
        :type old_state:
        :param new_state:
        :type new_state:
        """
        inst = self._setup(old_state, new_state)
        # Call
        ntools.assert_raises(StopIteration, inst._state_handler,
                             initial_state=old_state)
        # Tests
        connected = suspended = lost = 0
        if old_state == new_state:
            # In this case none of the state change handlers should be called
            pass
        elif new_state == libzk.KazooState.CONNECTED:
            connected = 1
        elif new_state == libzk.KazooState.SUSPENDED:
            suspended = 1
        elif new_state == libzk.KazooState.LOST:
            lost = 1
        else:
            raise SCIONTestError("Invalid new state")
        ntools.eq_(inst._state_connected.call_count, connected)
        ntools.eq_(inst._state_suspended.call_count, suspended)
        ntools.eq_(inst._state_lost.call_count, lost)

    def test(self):
        """
        Run main tests.
        """
        test_inputs = (
            (libzk.KazooState.CONNECTED, libzk.KazooState.CONNECTED),
            ("startup", libzk.KazooState.CONNECTED),
            (libzk.KazooState.CONNECTED, libzk.KazooState.SUSPENDED),
            (libzk.KazooState.CONNECTED, libzk.KazooState.LOST),
            (libzk.KazooState.SUSPENDED, libzk.KazooState.CONNECTED),
            (libzk.KazooState.SUSPENDED, libzk.KazooState.LOST),
            (libzk.KazooState.LOST, libzk.KazooState.CONNECTED),
        )
        for old_state, new_state, in test_inputs:
            yield self._check, old_state, new_state


class TestZookeeperStateConnected(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper._state_connected
    """

    @mock_wrapper
    def test_basic(self):
        """
        Test basic functionalities.
        """
        inst = self._init_basic_setup()
        inst.ensure_path = MagicMock(spec_set=[])
        inst._parties = {
            "/patha": MagicMock(spec_set=["autojoin"]),
            "/pathb": MagicMock(spec_set=["autojoin"]),
        }
        # Call
        inst._state_connected()
        # Tests
        inst.ensure_path.assert_called_once_with(inst._prefix, abs=True)
        inst._parties["/patha"].autojoin.assert_called_once_with()
        inst._parties["/pathb"].autojoin.assert_called_once_with()
        inst._connected.set.assert_called_with()

    @mock_wrapper
    def test_ensure_paths(self):
        """
        Test ...
        """
        inst = self._init_basic_setup(ensure_paths=("asfw", "weasg"))
        inst.ensure_path = MagicMock(spec_set=[])
        # Call
        inst._state_connected()
        # Tests
        inst.ensure_path.assert_has_calls([
            call(inst._prefix, abs=True), call("asfw"), call("weasg")])

    @mock_wrapper
    def test_connectionloss(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        # Raise ConnectionLoss when _zk.ensure_path() is called
        inst._zk.ensure_path.side_effect = [libzk.ConnectionLoss]
        # Call
        ntools.assert_false(inst._state_connected())

    @mock_wrapper
    def test_sessionexpired(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        # Raise SessionExpiredError when _zk.ensure_path() is called
        inst._zk.ensure_path.side_effect = [libzk.SessionExpiredError]
        # Call
        ntools.assert_false(inst._state_connected())

    @mock_wrapper
    def test_on_connect(self):
        """
        Test ...
        """
        on_c = MagicMock(spec_set=[])
        inst = self._init_basic_setup(on_connect=on_c)
        # Call
        inst._state_connected()
        # Tests
        on_c.assert_called_with()


class TestZookeeperStateDisconnected(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper._state_suspended AND
    lib.zookeeper.Zookeeper._state_lost

    _state_suspended and _state_lost currently have almost identical code, so
    test them both in the same way.
    """

    @mock_wrapper
    def _check(self, f_name, test_callback=False):
        """
        Test ...

        :param f_name:
        :type f_name:
        :param test_callback:
        :type test_callback:
        """
        on_d = None
        if test_callback:
            on_d = MagicMock(spec_set=[])
        inst = self._init_basic_setup(on_disconnect=on_d)
        # Call
        getattr(inst, f_name)()
        # Tests
        inst._connected.clear.assert_called_with()
        inst._lock.clear.assert_called_with()
        if test_callback:
            on_d.assert_called_with()

    def test(self):
        """
        Test with and without a callback function defined
        """
        for f in "_state_suspended", "_state_lost":
            yield self._check, f, True
            yield self._check, f


class TestZookeeperConnection(BaseZookeeper):
    """
    Unit tests for lib.zookeeper connection methods.
    """

    @mock_wrapper
    def _is_connected_check(self, connected):
        """
        Test ...

        :param connected:
        :type connected:
        """
        inst = self._init_basic_setup()
        inst._connected.is_set.return_value = connected
        # Call
        ntools.eq_(inst.is_connected(), connected)
        # Tests
        inst._connected.is_set.assert_called_with()

    def test_is_connected(self):
        """
        Test ...
        """
        for connected in True, False:
            yield self._is_connected_check, connected

    @mock_wrapper
    def _wait_connected_check(self, timeout):
        """
        Test ...

        :param timeout:
        :type timeout:
        """
        inst = self._init_basic_setup()
        inst._connected.wait.return_value = 33
        # Call
        ntools.eq_(inst.wait_connected(timeout=timeout), 33)
        # Tests
        inst._connected.wait.assert_called_with(timeout=timeout)

    def test_wait_connected(self):
        """
        Test ...
        """
        for timeout in None, 1, 15:
            yield self._wait_connected_check, timeout


class TestZookeeperEnsurePath(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.ensure_path
    """
    @mock_wrapper
    def test_basic(self):
        # Setup
        inst = self._init_basic_setup()
        # Call
        inst.ensure_path("pathness")
        # Tests
        inst._zk.ensure_path.assert_called_once_with(
            "%s/%s" % (inst._prefix, "pathness"))

    @mock_wrapper
    def test_abs(self):
        # Setup
        inst = self._init_basic_setup()
        # Call
        inst.ensure_path("/path/to/stuff", abs=True)
        # Tests
        inst._zk.ensure_path.assert_called_once_with(
            "/path/to/stuff")

    @mock_wrapper
    def _check_error(self, excp):
        # Setup
        inst = self._init_basic_setup()
        inst._zk.ensure_path.side_effect = excp
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss, inst.ensure_path, "asdwaf")

    def test_errors(self):
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._check_error, excp


class TestZookeeperPartySetup(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.party_setup
    """
    @mock_wrapper
    def test_not_connected(self):
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss, inst.party_setup)
        # Tests
        inst.is_connected.assert_called_once_with()

    @patch("lib.zookeeper.ZkParty", autospec=True)
    @mock_wrapper
    def test_basic(self, zkparty):
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        # Call
        inst.party_setup()
        # Tests
        zkparty.assert_called_once_with(inst._zk, "%s/party" % inst._prefix,
                                        inst._srv_id, True)

    @patch("lib.zookeeper.ZkParty", autospec=True)
    @mock_wrapper
    def test_full(self, zkparty):
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        # Call
        inst.party_setup("/pref", False)
        # Tests
        zkparty.assert_called_once_with(inst._zk, "/pref/party", inst._srv_id,
                                        False)


class TestZookeeperGetLock(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.get_lock
    """

    @mock_wrapper
    def test_no_lock(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        # Short-circuit the rest of get_lock() by making is_connected raise
        # StopIteration.
        inst.is_connected = MagicMock(spec_set=[], side_effect=[])
        # Call
        ntools.assert_raises(StopIteration, inst.get_lock)
        # Tests
        self.mocks.klock.assert_called_once_with("%s/lock" % inst._prefix,
                                                 inst._srv_id)

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        inst.release_lock = MagicMock(spec_set=[])
        inst._lock.is_set.side_effect = SCIONTestError(
            "this should not have been reached")
        # Call
        ntools.assert_false(inst.get_lock())
        # Tests
        inst.is_connected.assert_called_once_with()
        inst.release_lock.assert_called_once_with()

    @mock_wrapper
    def test_have_lock(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._lock.is_set.return_value = True
        inst._zk_lock = self.mocks.klock
        inst._zk_lock.acquire.side_effect = SCIONTestError(
            "_zk_lock.acquire should not have been reached")
        # Call
        ntools.assert_true(inst.get_lock())
        # Tests
        inst._lock.is_set.assert_called_once_with()

    @mock_wrapper
    def test_acquire(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._lock.is_set.return_value = False
        inst._zk_lock = self.mocks.klock
        inst._zk_lock.acquire.return_value = True
        inst.have_lock = MagicMock(spec_set=[], return_value=True)
        # Call
        ntools.assert_true(inst.get_lock())
        # Tests
        inst._zk_lock.acquire.assert_called_once_with(timeout=60.0)
        inst._lock.set.assert_called_once_with()
        inst.have_lock.assert_called_once_with()

    @mock_wrapper
    def _acquire_exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._lock.is_set.return_value = False
        inst._zk_lock = self.mocks.klock
        inst._zk_lock.acquire.side_effect = exception
        # Call
        ntools.assert_false(inst.get_lock())
        # Tests
        inst._zk_lock.acquire.assert_called_once_with(timeout=60.0)

    def test_acquire_exceptions(self):
        """
        Test ...
        """
        for excp in (libzk.LockTimeout, libzk.ConnectionLoss,
                     libzk.SessionExpiredError):
            yield self._acquire_exception_check, excp


class TestZookeeperReleaseLock(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.release_lock
    """

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        inst._zk_lock = self.mocks.klock
        inst._zk_lock.is_acquired = True
        inst._zk_lock.release.side_effect = SCIONTestError(
            "_zk_lock.release() shouldn't have been called")
        # Call
        inst.release_lock()
        # Tests
        inst._lock.clear.assert_called_once_with()
        inst.is_connected.assert_called_once_with()
        ntools.assert_false(inst._zk_lock.is_acquired)

    @mock_wrapper
    def test_release(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk_lock = self.mocks.klock
        # Call
        inst.release_lock()
        # Tests
        inst._zk_lock.release.assert_called_once_with()

    @mock_wrapper
    def _exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk_lock = self.mocks.klock
        inst._zk_lock.is_acquired = True
        inst._zk_lock.release.side_effect = exception
        # Call
        inst.release_lock()
        # Tests
        ntools.assert_false(inst._zk_lock.is_acquired)

    def test_exceptions(self):
        """
        Test ...
        """
        for excp in (libzk.NoNodeError, libzk.ConnectionLoss,
                     libzk.SessionExpiredError):
            yield self._exception_check, excp


class TestZookeeperLockUtilities(BaseZookeeper):
    """
    Unit tests for lib.zookeeper locking utility methods.
    """

    @mock_wrapper
    def _have_lock_check(self, connected, have_lock):
        """
        Test ...

        :param connected:
        :type connected:
        :param have_lock:
        :type have_lock:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=connected)
        inst._lock.is_set.return_value = have_lock
        expected = connected and have_lock
        # Call
        ntools.eq_(inst.have_lock(), expected)
        # Tests
        inst.is_connected.assert_called_once_with()
        if connected:
            inst._lock.is_set.assert_called_once_with()

    def test_have_lock(self):
        """
        Test ...
        """
        for connected, have_lock in (
                (False, False),
                (False, True),
                (True, False),
                (True, True)):
            yield self._have_lock_check, connected, have_lock

    @mock_wrapper
    def test_wait_lock(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst._lock = self.mocks.pyevent
        # Call
        inst.wait_lock()
        # Tests
        inst._lock.wait.assert_called_once_with()


class TestZookeeperStoreSharedItems(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.store_shared_item
    """

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.store_shared_item, 'p', 'n', 'v')
        # Tests
        inst.is_connected.assert_called_once_with()

    @mock_wrapper
    def test_exists(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.create.side_effect = SCIONTestError(
            "_zk.create shouldn't have been called")
        # Call
        inst.store_shared_item('p', 'n', 'v')
        # Tests
        inst._zk.set.assert_called_once_with("%s/p/n" % inst._prefix, "v")

    @mock_wrapper
    def _exists_exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.set.side_effect = exception
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.store_shared_item, 'p', 'n', 'v')
        # Tests
        inst._zk.set.assert_called_once_with("%s/p/n" % inst._prefix, "v")

    def test_exists_exception(self):
        """
        Test ...
        """
        for i in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._exists_exception_check, i

    @mock_wrapper
    def test_create(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.set.side_effect = libzk.NoNodeError
        # Call
        inst.store_shared_item('p', 'n', 'v')
        # Tests
        inst._zk.create.assert_called_once_with("%s/p/n" % inst._prefix, "v")

    @mock_wrapper
    def _create_exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.set.side_effect = libzk.NoNodeError
        inst._zk.create.side_effect = exception
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.store_shared_item, 'p', 'n', 'v')
        # Tests
        inst._zk.create.assert_called_once_with("%s/p/n" % inst._prefix, "v")

    def test_create_exception(self):
        """
        Test ...
        """
        for i in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._create_exception_check, i


class TestZookeeperGetSharedItem(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.get_shared_item
    """

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.get_shared_item,
                             "path", "entry")
        # Tests
        inst.is_connected.assert_called_once_with()

    @mock_wrapper
    def test_success(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.get.return_value = ("nodedata", "metadata")
        # Call
        ntools.assert_equals(inst.get_shared_item("path", "entry"), "nodedata")
        # Tests
        inst._zk.get.assert_called_once_with("%s/%s/%s" %
                                             (inst._prefix, "path", "entry"))

    @mock_wrapper
    def _exception_check(self, exception, result):
        """
        Test ...

        :param exception:
        :type exception:
        :param result:
        :type result:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.get.side_effect = exception
        # Call
        ntools.assert_raises(result,
                             inst.get_shared_item, 'path', 'entry')
        # Tests
        inst._zk.get.assert_called_once_with("%s/%s/%s" %
                                             (inst._prefix, "path", "entry"))

    def test_exception(self):
        """
        Test ...
        """
        for excp, result in (
                (libzk.NoNodeError, libzk.ZkNoNodeError),
                (libzk.ConnectionLoss, libzk.ZkConnectionLoss),
                (libzk.SessionExpiredError, libzk.ZkConnectionLoss)):
            yield self._exception_check, excp, result


class TestZookeeperGetSharedMetadata(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.get_shared_metadata
    """

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        inst._zk.get_children.side_effect = SCIONTestError(
            "_zk.get_children should not have been reached")
        # Call
        ntools.eq_(inst.get_shared_metadata("path"), [])
        # Tests
        inst.is_connected.assert_called_once_with()

    @mock_wrapper
    def test_get(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.get_children.return_value = ["entry1", "entry2"]
        inst._zk.exists.side_effect = ["meta1", "meta2"]
        # Call
        ntools.eq_(inst.get_shared_metadata("path"),
                   [("entry1", "meta1"), ("entry2", "meta2")])
        # Tests
        inst._zk.get_children.assert_called_once_with("%s/path" % inst._prefix)
        inst._zk.exists.assert_has_calls([
            call("%s/path/entry1" % inst._prefix),
            call("%s/path/entry2" % inst._prefix)])

    @mock_wrapper
    def _get_children_exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.get_children.side_effect = exception
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.get_shared_metadata, "path")
        # Tests
        inst._zk.get_children.assert_called_once_with("%s/path" % inst._prefix)

    def test_get_children_exception(self):
        """
        Test ...
        """
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._get_children_exception_check, excp

    @mock_wrapper
    def _exists_exception_check(self, exception):
        """
        Test ...

        :param exception:
        :type exception:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst._zk.get_children.return_value = ["entry1", "entry2"]
        inst._zk.exists.side_effect = exception
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss,
                             inst.get_shared_metadata, "path")
        # Tests
        inst._zk.get_children.assert_called_once_with("%s/path" % inst._prefix)
        inst._zk.exists.assert_called_once_with(
            "%s/path/entry1" % inst._prefix)

    def test_exists_exception(self):
        """
        Test ...
        """
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._exists_exception_check, excp


class TestZookeeperExpireSharedItems(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.expire_shared_items
    """

    @mock_wrapper
    def test_not_connected(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=False)
        inst.get_shared_metadata = MagicMock(
            spec_set=[], side_effect=SCIONTestError(
                "get_shared_metadata shouldn't have been called"))
        # Call
        ntools.assert_is_none(inst.expire_shared_items("path", 100))

    @mock_wrapper
    def test_no_entries(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        inst.get_shared_metadata = MagicMock(spec_set=[], return_value=[])
        inst._zk.delete = MagicMock(
            spec_set=[], side_effect=SCIONTestError(
                "_zk.delete shouldn't have been called"))
        # Call
        ntools.eq_(inst.expire_shared_items("path", 100), 0)

    @mock_wrapper
    def test_expire(self):
        """
        Test ...
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        metadata = [
            ["entry1", MagicMock(spec_set=ZnodeStat, last_modified=1)],
            ["entry2", MagicMock(spec_set=ZnodeStat, last_modified=99)],
            ["entry3", MagicMock(spec_set=ZnodeStat, last_modified=101)],
            ["entry4", MagicMock(spec_set=ZnodeStat, last_modified=10000)],
        ]
        inst.get_shared_metadata = MagicMock(
            spec_set=[], return_value=metadata)
        # Call
        ntools.eq_(inst.expire_shared_items("path", 100), 2)
        # Tests
        inst._zk.delete.assert_has_calls([
            call("%s/path/entry1" % inst._prefix),
            call("%s/path/entry2" % inst._prefix)])

    @mock_wrapper
    def _exception_check(self, exception, result):
        """
        Test ...

        :param exception:
        :type exception:
        :param result:
        :type result:
        """
        inst = self._init_basic_setup()
        inst.is_connected = MagicMock(spec_set=[], return_value=True)
        metadata = [["entry1", MagicMock(spec_set=ZnodeStat, last_modified=1)]]
        inst.get_shared_metadata = MagicMock(
            spec_set=[], return_value=metadata)
        inst._zk.delete.side_effect = exception
        # Call
        ntools.assert_raises(
            result, inst.expire_shared_items, "path", 100)

    def test_exceptions(self):
        """
        Test ...
        """
        for excp, result in (
                (libzk.ConnectionLoss, libzk.ZkConnectionLoss),
                (libzk.SessionExpiredError, libzk.ZkConnectionLoss),
                (libzk.NoNodeError, libzk.ZkNoNodeError)):
            yield self._exception_check, excp, result


class TestZookeeperRetry(BaseZookeeper):
    """
    Unit tests for lib.zookeeper.Zookeeper.retry
    """
    @mock_wrapper
    def test_basic(self):
        inst = self._init_basic_setup()
        inst.wait_connected = MagicMock(spec_set=[])
        f = MagicMock(spec_set=[])
        # Call
        ntools.eq_(inst.retry("desc", f, "arg1", _timeout=5.4, kwarg1="k"),
                   f.return_value)
        # Tests
        inst.wait_connected.assert_called_once_with(timeout=5.4)
        f.assert_called_once_with("arg1", kwarg1="k")

    @mock_wrapper
    def test_no_conn(self):
        inst = self._init_basic_setup()
        inst.wait_connected = MagicMock(spec_set=[])
        inst.wait_connected.return_value = False
        f = MagicMock(spec_set=[])
        # Call
        ntools.assert_raises(libzk.ZkRetryLimit, inst.retry, "desc", f)
        # Tests
        inst.wait_connected.assert_has_calls([call(timeout=10.0)] * 5)
        ntools.eq_(inst.wait_connected.call_count, 5)
        ntools.assert_false(f.called)

    @mock_wrapper
    def test_no_retries(self):
        inst = self._init_basic_setup()
        inst.wait_connected = MagicMock(spec_set=[])
        inst.wait_connected.return_value = False
        f = MagicMock(spec_set=[])
        # Call
        ntools.assert_raises(libzk.ZkRetryLimit, inst.retry, "desc", f,
                             _retries=0)
        # Tests
        inst.wait_connected.assert_called_once_with(timeout=10.0)

    @mock_wrapper
    def test_inf_retries(self):
        inst = self._init_basic_setup()
        inst.wait_connected = MagicMock(spec_set=[])
        inst.wait_connected.side_effect = [False] * 20
        f = MagicMock(spec_set=[])
        # Call
        ntools.assert_raises(StopIteration, inst.retry, "desc", f,
                             _retries=None)
        # Tests
        inst.wait_connected.assert_has_calls([call(timeout=10.0)] * 21)
        ntools.eq_(inst.wait_connected.call_count, 21)

    @mock_wrapper
    def test_conn_drop(self):
        inst = self._init_basic_setup()
        inst.wait_connected = MagicMock(spec_set=[])
        inst.wait_connected.return_value = True
        f = MagicMock(spec_set=[])
        f.side_effect = [libzk.ZkConnectionLoss, "success"]
        # Call
        ntools.eq_(inst.retry("desc", f), "success")
        # Tests
        inst.wait_connected.assert_has_calls([call(timeout=10.0)] * 2)
        ntools.eq_(inst.wait_connected.call_count, 2)


class TestZkPartyInit(object):
    """
    Unit tests for lib.zookeeper.ZkParty.__init__
    """
    @patch("lib.zookeeper.ZkParty.autojoin", autospec=True)
    def test_basic(self, autojoin):
        zk = MagicMock(spec_set=["Party"])
        # Call
        p = libzk.ZkParty(zk, "path", "id", "autojoin")
        # Tests
        ntools.eq_(p._autojoin, "autojoin")
        ntools.eq_(p._path, "path")
        zk.Party.assert_called_once_with("path", "id")
        ntools.eq_(p._party, zk.Party.return_value)
        autojoin.assert_called_once_with(p)

    @patch("lib.zookeeper.ZkParty.autojoin", autospec=True)
    def _check_error(self, excp, autojoin):
        zk = MagicMock(spec_set=["Party"])
        zk.Party.side_effect = excp
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss, libzk.ZkParty, zk, "path",
                             "id", True)

    def test_error(self):
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._check_error, excp


class TestZkPartyJoin(object):
    """
    Unit tests for lib.zookeeper.ZkParty.join
    """
    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def test_basic(self, init):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._party = MagicMock(spec_set=["join"])
        p.list = MagicMock(spec_set=[])
        # Call
        p.join()
        # Tests
        p._party.join.assert_called_once_with()

    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def _check_error(self, excp, init):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._party = MagicMock(spec_set=["join"])
        p._party.join.side_effect = excp
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss, p.join)

    def test_error(self):
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._check_error, excp


class TestZkPartyAutoJoin(object):
    """
    Unit tests for lib.zookeeper.ZkParty.autojoin
    """
    @patch("lib.zookeeper.ZkParty.list", autospec=True)
    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def test_auto(self, init, list_):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._autojoin = True
        p.join = MagicMock(spec_set=[])
        p._path = "path"
        # Call
        p.autojoin()
        # Tests
        p.join.assert_called_once_with()

    @patch("lib.zookeeper.ZkParty.list", autospec=True)
    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def test_noauto(self, init, list_):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._autojoin = False
        p.join = MagicMock(spec_set=[])
        p._path = "path"
        # Call
        p.autojoin()
        # Tests
        ntools.assert_false(p.join.called)


class TestZkPartyList(object):
    """
    Unit tests for lib.zookeeper.ZkParty.list
    """
    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def test_basic(self, init):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._party = MagicMock(spec_set=["__iter__"])
        p._party.__iter__.return_value = [1, 2, 3]
        # Call
        ntools.eq_(p.list(), {1, 2, 3})

    @patch("lib.zookeeper.ZkParty.__init__", autospec=True, return_value=None)
    def _check_error(self, excp, init):
        p = libzk.ZkParty("zk", "path", "id", "autojoin")
        p._party = MagicMock(spec_set=["__iter__"])
        p._party.__iter__.side_effect = excp
        # Call
        ntools.assert_raises(libzk.ZkConnectionLoss, p.list)

    def test_error(self):
        for excp in libzk.ConnectionLoss, libzk.SessionExpiredError:
            yield self._check_error, excp


if __name__ == "__main__":
    nose.run(defaultTest=__name__)
