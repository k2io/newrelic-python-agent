import aiohttp
import asyncio
import pytest

from newrelic.api.background_task import background_task
from newrelic.api.function_trace import function_trace
from testing_support.fixtures import validate_transaction_metrics

version_info = tuple(int(_) for _ in aiohttp.__version__.split('.')[:2])
skipif_aiohttp3 = pytest.mark.skipif(version_info >= (3, 0),
        reason='This version of aiohttp does not support yield from syntax')


@asyncio.coroutine
def fetch(method, url):
    with aiohttp.ClientSession() as session:
        _method = getattr(session, method)
        response = yield from asyncio.wait_for(_method(url), timeout=None)
        response.raise_for_status()
        yield from response.text()


@asyncio.coroutine
def fetch_multiple(method, url):
    coros = [fetch(method, url) for _ in range(2)]
    return asyncio.gather(*coros, return_exceptions=True)


if version_info < (2, 0):
    _expected_error_class = aiohttp.errors.HttpProcessingError
else:
    _expected_error_class = aiohttp.client_exceptions.ClientResponseError


def task(loop, method, exc_expected, url):
    future = asyncio.ensure_future(fetch_multiple(method, url))
    text_list = loop.run_until_complete(future)
    if exc_expected:
        assert isinstance(text_list[0], _expected_error_class)
        assert isinstance(text_list[1], _expected_error_class)
    else:
        assert text_list[0] == text_list[1]


test_matrix = (
    ('get', False),
    ('post', True),
    ('options', True),
    ('head', True),
    ('put', True),
    ('patch', True),
    ('delete', True),
)


@pytest.mark.xfail(
        reason="Parenting is not correct for concurrent fetch while we "
               "transition to a trace cache.", strict=True)
@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_client_yield_from(local_server_info, method, exc_expected):
    @validate_transaction_metrics(
        'test_client_yield_from',
        background_task=True,
        scoped_metrics=[
            (local_server_info.base_metric + method.upper(), 2),
        ],
        rollup_metrics=[
            (local_server_info.base_metric + method.upper(), 2),
        ],
    )
    @background_task(name='test_client_yield_from')
    def task_test():
        loop = asyncio.get_event_loop()
        task(loop, method, exc_expected, local_server_info.url)

    task_test()


@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_client_no_txn_yield_from(local_server_info, method, exc_expected):

    def task_test():
        loop = asyncio.get_event_loop()
        task(loop, method, exc_expected, local_server_info.url)

    task_test()


@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_client_throw_yield_from(local_server_info, method, exc_expected):

    class ThrowerException(ValueError):
        pass

    @asyncio.coroutine
    def self_driving_thrower():
        with aiohttp.ClientSession() as session:
            coro = session._request(method.upper(), local_server_info.url)

            # activate the coroutine
            coro.send(None)

            # inject error
            coro.throw(ThrowerException())

    @validate_transaction_metrics(
        'test_client_throw_yield_from',
        background_task=True,
        scoped_metrics=[
            (local_server_info.base_metric + method.upper(), 1),
        ],
        rollup_metrics=[
            (local_server_info.base_metric + method.upper(), 1),
        ],
    )
    @background_task(name='test_client_throw_yield_from')
    def task_test():
        loop = asyncio.get_event_loop()

        with pytest.raises(ThrowerException):
            loop.run_until_complete(self_driving_thrower())

    task_test()


@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_client_close_yield_from(local_server_info, method, exc_expected):

    @asyncio.coroutine
    def self_driving_closer():
        with aiohttp.ClientSession() as session:
            coro = session._request(method.upper(), local_server_info.url)

            # activate the coroutine
            coro.send(None)

            # force close
            coro.close()

    @validate_transaction_metrics(
        'test_client_close_yield_from',
        background_task=True,
        scoped_metrics=[
            (local_server_info.base_metric + method.upper(), 1),
        ],
        rollup_metrics=[
            (local_server_info.base_metric + method.upper(), 1),
        ],
    )
    @background_task(name='test_client_close_yield_from')
    def task_test():
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self_driving_closer())

    task_test()


test_ws_matrix = (
    # the 127.0.0.1 server does not accept websocket requests, hence an
    # exception is expected but a metric will still be created
    ('ws_connect', True),
)


@pytest.mark.xfail(
        reason="Parenting is not correct for concurrent fetch while we "
               "transition to a trace cache.", strict=True)
@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_ws_matrix)
def test_ws_connect_yield_from(local_server_info, method, exc_expected):

    @validate_transaction_metrics(
        'test_ws_connect_yield_from',
        background_task=True,
        scoped_metrics=[
            (local_server_info.base_metric + 'GET', 2),
        ],
        rollup_metrics=[
            (local_server_info.base_metric + 'GET', 2),
        ],
    )
    @background_task(name='test_ws_connect_yield_from')
    def task_test():
        loop = asyncio.get_event_loop()
        task(loop, method, exc_expected, local_server_info.url)

    task_test()


@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_create_task_yield_from(local_server_info, method, exc_expected):

    # `loop.create_task` returns a Task object which uses the coroutine's
    # `send` method, not `__next__`

    @asyncio.coroutine
    def fetch_task(loop):
        with aiohttp.ClientSession() as session:
            coro = getattr(session, method)
            resp = yield from loop.create_task(coro(local_server_info.url))
            resp.raise_for_status()
            yield from resp.text()

    @asyncio.coroutine
    def fetch_multiple(loop):
        coros = [fetch_task(loop) for _ in range(2)]
        return asyncio.gather(*coros, return_exceptions=True)

    @validate_transaction_metrics(
        'test_create_task_yield_from',
        background_task=True,
        scoped_metrics=[
            (local_server_info.base_metric + method.upper(), 2),
        ],
        rollup_metrics=[
            (local_server_info.base_metric + method.upper(), 2),
        ],
    )
    @background_task(name='test_create_task_yield_from')
    def task_test():
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(fetch_multiple(loop))
        if exc_expected:
            assert isinstance(result[0], _expected_error_class)
            assert isinstance(result[1], _expected_error_class)
        else:
            assert result[0] == result[1]

    task_test()


@skipif_aiohttp3
@pytest.mark.parametrize('method,exc_expected', test_matrix)
def test_terminal_node_yield_from(local_server_info, method, exc_expected):
    """
    This test injects a terminal node into a simple background task workflow.
    It was added to validate a bug where our coro.send() wrapper would fail
    when transaction's current node was terminal.
    """

    @background_task()
    def task_test():
        loop = asyncio.get_event_loop()

        @function_trace(terminal=True)
        def execute_task():
            task(loop, method, exc_expected, local_server_info.url)

        execute_task()

    task_test()
