"""Every error this package raises, in one place.

All of them derive from :class:`EarningSpyError`, so one ``except`` covers the
package. A message says what happened and what to do next; a call that reached
the service also carries ``status_code`` and ``request_id`` to quote to support.
"""


class EarningSpyError(RuntimeError):
    """Something went wrong. Base of every error below."""

    def __init__(self, *args, status_code=None, request_id=None):
        super().__init__(*args)
        #: The HTTP status of the refused call, or None when there was no response.
        self.status_code = status_code
        #: The service's id for the call, for a support request. None when unknown.
        self.request_id = request_id


class AuthError(EarningSpyError):
    """The key is missing or not accepted, or does not carry the tier asked for."""


class TierError(AuthError):
    """The key was refused for this view: it may not carry the tier asked for."""


class NotFoundError(EarningSpyError):
    """No such ticker, or nothing on file for it."""


class RequestError(EarningSpyError):
    """The request itself was rejected — a bad parameter or the wrong view."""


class RateLimitError(RequestError):
    """Too many requests, or the key's quota is used up.

    ``retry_after`` is the wait the service asked for, in seconds, when it gave one;
    ``quota_spent`` says waiting a moment will not help.
    """

    def __init__(self, *args, retry_after=None, quota_spent=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after
        self.quota_spent = quota_spent


class ServiceError(EarningSpyError):
    """The service could not be reached, or failed to answer."""


class ContractError(EarningSpyError):
    """The response came back in a shape or vintage this client cannot trust."""


class ChartError(EarningSpyError, ValueError):
    """A chart was asked for something the payload does not, or cannot, provide.

    Raised on a broken contract — a metric this client has not subscribed, a
    subscribed metric the payload failed to deliver, an axis the response does
    not carry. NOT raised when a valid request simply finds no data to draw: an
    empty peer group is a real answer and renders as an empty figure, never an
    exception.
    """


class PayloadNotice(UserWarning):
    """A note the API attached to a response — a thin distribution, a stale
    figure. Informational, not an error: the chart still renders. Its own
    category so a caller can silence just these (``warnings.filterwarnings(
    "ignore", category=PayloadNotice)``) without hiding version-skew warnings.
    describe() also lists them, and they stay in ``payload["warnings"]``.
    """
