"""List-management constants for Google Maps driver."""

SAVED_TAB_SELECTORS = (
    "button[aria-label*='Saved']",
    "button[aria-label*='Your places']",
    "button:has-text('Saved')",
)
LISTS_TAB_SELECTORS = (
    "[role='tab']:has-text('Lists')",
)
SAVED_VIEW_MORE_SELECTORS = (
    "button:has-text('View more')",
    "button:has-text('Show more')",
    "button:has-text('More')",
)
NEW_LIST_BUTTON_SELECTORS = (
    "button:has-text('New list')",
    "button:has-text('New List')",
    "button[aria-label*='New list']",
    "button:has-text('Create list')",
    "button:has-text('Create List')",
    "button[aria-label*='new list' i]",
    "button[aria-label*='create list' i]",
    "button[jsaction*='newList']",
    "button[jsaction*='createList']",
    "button:has(span:has-text('add'))",
    "button:has-text('\ue1d3')",
    "button:has-text('\ue145')",
)
LIST_CREATION_ENTRY_SELECTORS = (
    "div[role='dialog'] button:has-text('Private')",
    "div[role='dialog'] button:has-text('Personal')",
    "div[role='dialog'] button:has-text('Create list')",
    "div[role='dialog'] button:has-text('Next')",
    "div[role='dialog'] [role='button']:has-text('Private')",
    "div[role='dialog'] [role='button']:has-text('Create list')",
    "div[role='dialog'] [role='button']:has-text('Next')",
)
LIST_NAME_INPUT_SELECTORS = (
    "div[role='dialog'] input[aria-label*='list' i]",
    "div[role='dialog'] textarea[aria-label*='list' i]",
    "div[role='dialog'] input[type='text']",
    "div[role='dialog'] textarea",
    "div[role='dialog'] [contenteditable='true']",
    "div[role='dialog'] [role='textbox']",
)
CREATE_LIST_BUTTON_SELECTORS = (
    "div[role='dialog'] button:has-text('Create')",
    "div[role='dialog'] button:has-text('Done')",
    "div[role='dialog'] button:has-text('Save')",
    "[role='dialog'] button:has-text('Create')",
    "[role='dialog'] button:has-text('Done')",
    "[role='dialog'] button:has-text('Save')",
)
UNTITLED_LIST_ENTRY_TOKENS = (
    "Untitled list",
)
SAVED_PANEL_READY_TIMEOUT_MS = 5000
LIST_CREATION_READY_TIMEOUT_MS = 5000
LIST_OPEN_READY_TIMEOUT_MS = 5000
UI_ACTION_POLL_INTERVAL_MS = 150
