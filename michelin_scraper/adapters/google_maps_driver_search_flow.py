"""Search-flow constants for Google Maps driver."""

SEARCH_LOADING_INDICATOR_SELECTORS = (
    "div[role='search'] .lSDxNd",
    "div[role='search'] .lSDxNd .q0z1yb",
    "div[role='search'] [role='progressbar']",
)
NO_RESULTS_TEXT_TOKENS = (
    "no results found",
    "did not match any locations",
    "couldn't find",
    "could not find",
)
SEARCH_OUTCOME_RESULT_READY = "result_ready"
SEARCH_OUTCOME_NO_RESULTS = "no_results"
SEARCH_OUTCOME_TIMEOUT_MS = 5000
SEARCH_OUTCOME_MAX_TIMEOUT_MS = 15000
SEARCH_OUTCOME_POLL_INTERVAL_MS = 150
SEARCH_OUTCOME_MIN_SETTLE_MS = 1200
SEARCH_RESULT_CLICK_TIMEOUT_MS = 2500
SEARCH_RESULT_CLICK_MAX_ATTEMPTS = 3
SEARCH_INPUT_READY_TIMEOUT_MS = 2500
MAPS_OPEN_MAX_ATTEMPTS = 3
SECURITY_BLOCK_MAIN_TOKEN = "browser or app may not be secure"
SECURITY_BLOCK_SIGNIN_TOKENS = (
    "couldn't sign you in",
    "couldn\u2019t sign you in",
)
