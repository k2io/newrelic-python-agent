import copy
import inspect
import pytest
import logging

from newrelic.common.object_names import callable_name
from newrelic.common.object_wrapper import (function_wrapper,
        wrap_function_wrapper)

from testing_support.fixtures import _validate_node_parenting

_logger = logging.getLogger('newrelic.tests.tornado')

# The following fixtures and validation functions are used in the Tornado 4
# instrumentation.

# To validate the contents of a recorded transaction one would use the fixtures
# wrap_record_transaction_fixture and clear_record_transaction_list. The first
# fixture runs once per test session and wraps record_transaction so it writes
# to _RECORDED_TRANSACTIONS. The clear_record_transaction_list ensures that
# the transactions are cleared out for each test.

# _RECORDED_TRANSACTIONS is a list of all the transaction recorded during a
# test. It gets cleared at the beginning of each test by the
# clear_record_transaction_list fixture.

# We also want to keep track of the number of times we try to finalize a
# transaction, so we can check that this matches the number of times we expect,
# and should be equal to the number of transactions in _RECORDED_TRANSACTIONS.
# Otherwise, it means we have attempted to record a transaction more that once,
# which is considered and error (although it won't raise one)

_RECORDED_TRANSACTIONS = []
_NUM_FINIALIZED_TRANSACTIONS = 0


@pytest.fixture(scope='function')
def clear_record_transaction_list():
    global _RECORDED_TRANSACTIONS
    global _NUM_FINIALIZED_TRANSACTIONS
    # Truncate the _RECORDED_TRANSACTIONS list. This actually assigns
    # _RECORDED_TRANSACTIONS a new, empty list so it is a new object.
    # This is fine since _RECORDED_TRANSACTIONS is private to this module, but
    # could lead to problems if one tries to export it.
    _RECORDED_TRANSACTIONS = []
    _NUM_FINIALIZED_TRANSACTIONS = 0


@pytest.fixture(scope='session')
def wrap_record_transaction_fixture(request):

    def _nr_wrapper_record_transaction(wrapped, instance, args, kwargs):
        def _bind_params(transaction, *args, **kwargs):
            return transaction

        transaction = _bind_params(*args, **kwargs)
        errors = sorted([(e.type, e.message) for e in transaction.errors])

        try:
            result = wrapped(*args, **kwargs)
        except:
            raise

        # Record the original metric values (before they can be mutated)
        metrics = {}
        for k, m in instance.stats_table.items():
            metrics[k] = copy.copy(m)
        _RECORDED_TRANSACTIONS.append((transaction, metrics, errors))

        return result

    wrap_function_wrapper('newrelic.core.stats_engine',
            'StatsEngine.record_transaction', _nr_wrapper_record_transaction)


@pytest.fixture(scope='session')
def wrap_transaction_exit_fixture(request):

    def _nr_wrapper_Transaction__exit__(wrapped, instance, args, kwargs):
        global _NUM_FINIALIZED_TRANSACTIONS
        _NUM_FINIALIZED_TRANSACTIONS += 1

        return wrapped(*args, **kwargs)

    wrap_function_wrapper('newrelic.api.transaction',
            'Transaction.__exit__', _nr_wrapper_Transaction__exit__)


# _RECORDED_APP_EXCEPTIONS is used to store exceptions occurring outside of
# a transaction. Otherwise, the strategy for recording them is the same as
# _RECORDED_TRANSACTIONS.
_RECORDED_APP_EXCEPTIONS = []


@pytest.fixture(scope='function')
def clear_record_app_exception_list():
    global _RECORDED_APP_EXCEPTIONS
    _RECORDED_APP_EXCEPTIONS = []


@pytest.fixture(scope='session')
def wrap_record_app_exception_fixture(request):

    def _nr_wrapper_record_exception(wrapped, instance, args, kwargs):
        def _bind_params(exc, value, *args, **kwargs):
            return value

        value = _bind_params(*args, **kwargs)
        fullname = callable_name(value)

        try:
            result = wrapped(*args, **kwargs)
        except:
            raise

        _RECORDED_APP_EXCEPTIONS.append(fullname)

        return result

    wrap_function_wrapper('newrelic.core.stats_engine',
            'StatsEngine.record_exception', _nr_wrapper_record_exception)


# _RECORDED_UNSCOPED_METRIC_COUNTS is used to store unscoped metric counts.
_RECORDED_UNSCOPED_METRIC_COUNTS = {}


@pytest.fixture(scope='function')
def clear_record_unscoped_metrics():
    global _RECORDED_UNSCOPED_METRIC_COUNTS
    _RECORDED_UNSCOPED_METRIC_COUNTS = {}


@pytest.fixture(scope='session')
def wrap_record_unscoped_time_metrics():

    def _nr_wrapper_record_time_metric(wrapped, instance, args, kwargs):
        # key is (name, scope), for unscoped metrics scope == ''
        key = wrapped(*args, **kwargs)
        if key[1] == '':
            if key not in _RECORDED_UNSCOPED_METRIC_COUNTS:
                _RECORDED_UNSCOPED_METRIC_COUNTS[key] = 1
            else:
                _RECORDED_UNSCOPED_METRIC_COUNTS[key] += 1

        return key

    wrap_function_wrapper('newrelic.core.stats_engine',
            'StatsEngine.record_time_metric', _nr_wrapper_record_time_metric)


def tornado_validate_unscoped_metrics(unscoped_metrics):
    """Decorate to validate unscoped count metrics.

    Arguments:
      unscoped_metrics: A 2-tuple, (metric_name, count)

    """

    @function_wrapper
    def _validate(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)

        for name, count in unscoped_metrics:
            key = (name, '')
            assert key in _RECORDED_UNSCOPED_METRIC_COUNTS, (
                    '%s not in recorded unscoped metrics: %s' %
                    (name, _RECORDED_UNSCOPED_METRIC_COUNTS,))
            assert count == _RECORDED_UNSCOPED_METRIC_COUNTS[key], (
                    'Count for %s, %s  not correct: %s' %
                    (name, count, _RECORDED_UNSCOPED_METRIC_COUNTS,))

    return _validate


def tornado_validate_count_transaction_metrics(name, group='Function',
        background_task=False, scoped_metrics=[], rollup_metrics=[],
        custom_metrics=[], forgone_metric_substrings=[], transaction_count=1):
    """Decorator to validates count metrics.

    Arguments:
      name: metric name
      background_task: A boolean. If True the top level path of the metric
          is OtherTransaction. Otherwise it is WebTransaction.
      group: The second level path of the metric.
      scoped_metrics: A list of 2-tuples representing an expected metric:
        (scoped_metric_name, expected_count)
      rollup_metrics: A list of 2-tuples representing a rollup metric:
        (rollup_metric_name, expected_count)
      custom_metrics: A list of 2-tuple representing a custom metric:
        (custom_metric_name, expected_count)
      forgone_metric_substrings: A list of strings which should not be in
        any of the recorded metrics.

    Note, this only validates the first transaction in the test.

    """

    def _validate_metric_count(metrics, name, scope, count):
        key = (name, scope)
        metric = metrics.get(key)

        def _metrics_table():
            return 'metric=%r, metrics=%r' % (key, metrics)

        def _metric_details():
            return 'metric=%r, count=%r' % (key, metric.call_count)

        assert metric is not None, _metrics_table()
        assert metric.call_count == count, _metric_details()

    def _validate_forgone_metric_substrings(metrics, forgone_substrings):
        def _substring_found(substring, metric):
            return ('Found substring "%s" in metric "%s"' % (substring,
                metric))

        for foregone_substring in forgone_substrings:
            for key in metrics.keys():
                name, _ = key
                assert foregone_substring not in name, (
                        _substring_found(foregone_substring, name))

    @function_wrapper
    def _validate(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)

        if background_task:
            rollup_metric = 'OtherTransaction/all'
            transaction_metric = 'OtherTransaction/%s/%s' % (group, name)
        else:
            rollup_metric = 'WebTransaction'
            transaction_metric = 'WebTransaction/%s/%s' % (group, name)

        assert (_NUM_FINIALIZED_TRANSACTIONS == len(_RECORDED_TRANSACTIONS) ==
                transaction_count), ('Expected # of transactions=%d; '
                '# of recorded transactions=%d; # of times finalize called=%d'
                % (transaction_count, len(_RECORDED_TRANSACTIONS),
                 _NUM_FINIALIZED_TRANSACTIONS))

        # We only validate the first recorded transaction
        _, metrics, errors = _RECORDED_TRANSACTIONS[0]

        # validate top level metrics
        _validate_metric_count(metrics, rollup_metric, '', 1)
        _validate_metric_count(metrics, transaction_metric, '', 1)

        # validate passed in metrics
        for scoped_name, scoped_count in scoped_metrics:
            _validate_metric_count(
                    metrics, scoped_name, transaction_metric, scoped_count)

        for rollup_name, rollup_count in rollup_metrics:
            _validate_metric_count(metrics, rollup_name, '', rollup_count)

        for custom_name, custom_count in custom_metrics:
            _validate_metric_count(metrics, custom_name, '', custom_count)

        # validate forgone metrics regular expressions
        _validate_forgone_metric_substrings(metrics, forgone_metric_substrings)

    return _validate


def tornado_validate_time_transaction_metrics(name, group='Function',
        background_task=False, scoped_metrics=[], rollup_metrics=[],
        custom_metrics=[]):
    """Decorator to validates time metrics.

    Arguments:
      name: metric name
      background_task: A boolean. If True the top level path of the metric
          is WebTransaction. Otherwise it is OtherTransaction.
      group: The second level path of the metric.
      scoped_metrics: A list of 2-tuples representing an expected metric:
        (scoped_metric_name, (min_time, max_time))
      rollup_metrics: A list of 2-tuples representing a rollup metric:
        (rollup_metric_name, (min_time, max_time))
      custom_metrics: A list of 2-tuple representing a custom metric:
        (custom_metric_name, (min_time, max_time))

    Note, this only validates the first transaction in the test.

    """

    def _validate_metric_times(metrics, name, scope, call_time_range):
        key = (name, scope)
        metric = metrics.get(key)

        min_call_time, max_call_time = call_time_range

        def _metrics_table():
            return 'metric=%r, metrics=%r' % (key, metrics)

        def _metric_details():
            return 'metric=%r, total_call_time=%r' % (
                key, metric.total_call_time)

        assert metric is not None, _metrics_table()
        assert metric.total_call_time >= min_call_time, (
            _metric_details())
        assert metric.total_call_time <= max_call_time, (
            _metric_details())

    @function_wrapper
    def _validate(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)

        # We only validate the first recorded transaction
        _, metrics, errors = _RECORDED_TRANSACTIONS[0]

        for scoped_name, scoped_time_range in scoped_metrics:
            _validate_metric_times(
                    metrics, scoped_name, name, scoped_time_range)

        for rollup_name, rollup_time_range in rollup_metrics:
            _validate_metric_times(metrics, rollup_name, '', rollup_time_range)

        for custom_name, custom_time_range in custom_metrics:
            _validate_metric_times(metrics, custom_name, '', custom_time_range)

    return _validate


def tornado_validate_errors(errors=[], app_exceptions=[],
        expect_transaction=True):
    """Decorator to validate errors.

    Arguments:
      errors: A list of expected error types as strings. If empty this will
          test that no errors have occurred.

    Note, this only validates the first transaction in the test.

    """

    def _validate_transaction_errors():
        # We only validate the first recorded transaction
        _, metrics, errs = _RECORDED_TRANSACTIONS[0]

        # Sort captured errors. They are recorded in the format:
        # (type-string, error-message)
        # so we extract the first element and sort it.
        if len(errs):
            captured, _ = zip(*errs)
            captured = sorted(captured)
        else:
            captured = []

        # Sort expected errors
        expected = sorted(errors)

        assert expected == captured, 'expected=%r, captured=%r, errors=%r' % (
                expected, captured, errors)

    def _validate_app_exceptions():
        assert len(app_exceptions) == len(_RECORDED_APP_EXCEPTIONS)
        sorted_expected = sorted(app_exceptions)
        sorted_errors = sorted(_RECORDED_APP_EXCEPTIONS)
        for expect, err in zip(sorted_expected, sorted_errors):
            assert expect == err

    @function_wrapper
    def _validate_errors(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)
        if expect_transaction:
            _validate_transaction_errors()
        else:
            assert 0 == len(_RECORDED_TRANSACTIONS)
        _validate_app_exceptions()

    return _validate_errors


def tornado_validate_transaction_status(status_code):
    @function_wrapper
    def _validator(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)
        transaction_node, _, _ = _RECORDED_TRANSACTIONS[0]
        assert transaction_node.response_code == status_code

    return _validator


def tornado_run_validator(func):
    """Get the transaction_node saved in _RECORDED_TRANSACTIONS[0]
    and run func(transaction_node).

    If func returns True, test passes.

    """
    def _first_source_line(func):
        lines, line_num = inspect.getsourcelines(func)
        return 'FAILED at line %d: %s' % (line_num, lines[0].strip())

    @function_wrapper
    def _validator(wrapped, instance, args, kwargs):
        wrapped(*args, **kwargs)
        transaction_node, _, _ = _RECORDED_TRANSACTIONS[0]
        result = func(transaction_node)
        assert result, _first_source_line(func)

    return _validator


def tornado_validate_transaction_cache_empty():
    """Validates the transaction cache is empty after all requests are serviced.

    """

    @function_wrapper
    def validate_cache_empty(wrapped, instance, args, kwargs):
        from newrelic.core.transaction_cache import transaction_cache
        transaction = transaction_cache().current_transaction()
        assert transaction is None
        wrapped(*args, **kwargs)
        transaction = transaction_cache().current_transaction()
        assert transaction is None

    return validate_cache_empty


def tornado_validate_tt_parenting(expected_parenting):
    """
    Validate the parenting and start_time of each node in a transaction trace

    expected_parenting is a tuple. The second item is a list of child nodes.
    The first item is not used in validation and exists as a tool for the
    developer to differentiate the node tuples.

        expected_parenting_example = (
            'TransactionNode', [
                ('FunctionNode', [
                    ('FunctionNode', [
                        ('FunctionNode', []),
                        ('FunctionNode', []),
                    ]),
            ]),
        ])
    """
    @function_wrapper
    def _validate_tt_parenting(wrapped, instance, args, kwargs):
        try:
            result = wrapped(*args, **kwargs)
        except:
            raise
        finally:
            transaction, _, _ = _RECORDED_TRANSACTIONS[0]
            _validate_node_parenting(transaction, expected_parenting)

        return result

    return _validate_tt_parenting