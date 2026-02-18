"""Save-flow constants for Google Maps driver."""

SAVE_BUTTON_SELECTORS = (
    "div[role='main'] button[data-item-id='save']",
    "div[role='main'] button[jsaction*='save']",
    "div[role='main'] button[aria-label*='Save' i]",
    "div[role='main'] [role='button'][aria-label*='Save' i]",
    "div[role='main'] button[data-value='Save']",
    "div[role='main'] [role='button'][data-value='Save']",
    "div[role='main'] button:has-text('Save')",
    "div[role='main'] button:has-text('Saved')",
    "button[data-item-id='save']",
    "[role='button'][data-item-id='save']",
    "button[jsaction*='pane.save']",
    "button[aria-label*='Save' i]",
    "[role='button'][aria-label*='Save' i]",
    "button[data-value='Save']",
    "[role='button'][data-value='Save']",
    "button:has-text('Save')",
    "button:has-text('Saved')",
)
SAVE_CONTROL_TEXT_TOKENS = (
    "save",
    "saved",
)
SAVE_DIALOG_INTERACTIVE_SELECTORS = (
    "div[role='dialog'] [role='checkbox']",
    "div[role='dialog'] [role='menuitemcheckbox']",
    "div[role='dialog'] [role='menuitemradio']",
    "div[role='dialog'] [aria-checked='true']",
    "div[role='dialog'] [aria-checked='false']",
    "[role='menu'][aria-label*='Save' i] [role='menuitemradio']",
    "[role='menu'][aria-label*='Save' i] [role='menuitemcheckbox']",
    "[role='menu'][aria-label*='Save' i] [role='menuitem']",
    "[role='menu'] [role='menuitemradio'][aria-checked]",
    "[role='menu'] [role='menuitemcheckbox'][aria-checked]",
    "[role='menu'] [role='menuitem'][aria-checked]",
)
SAVE_DIALOG_NOTE_FIELD_SELECTORS = (
    "div[role='dialog'] textarea",
    "div[role='dialog'] input",
    "div[role='dialog'] [contenteditable='true']",
    "div[role='dialog'] [role='textbox']",
    "[role='menu'] textarea",
    "[role='menu'] input",
    "[role='menu'] [contenteditable='true']",
    "[role='menu'] [role='textbox']",
    "div[role='main'] textarea[aria-label*='note' i]",
    "div[role='main'] textarea[placeholder*='note' i]",
    "div[role='main'] [role='textbox'][aria-label*='note' i]",
)
SAVE_DIALOG_NOTE_KEYWORDS = (
    "note",
    "notes",
    "memo",
)
SAVE_DIALOG_LOADING_INDICATOR_SELECTORS = (
    "div[role='dialog'] [role='progressbar']",
    "div[role='dialog'] [aria-busy='true']",
    "[role='menu'][aria-busy='true']",
    "div[role='main'] [role='progressbar']",
    "div[role='main'] [aria-busy='true']",
    "div[role='main'] button:has-text('Saving')",
    "div[role='main'] [role='button']:has-text('Saving')",
    "div[role='main'] button:has-text('Saving…')",
    "div[role='main'] [role='button']:has-text('Saving…')",
)
SAVE_PANEL_SAVED_STATE_SELECTORS = (
    "div[role='main'] [aria-label*='Saved in' i]",
)
SAVE_PANEL_NOTE_EXPAND_SELECTORS = (
    "div[role='main'] button[aria-label*='place lists details' i]",
    "div[role='main'] [role='button'][aria-label*='place lists details' i]",
    "div[role='main'] button[aria-label*='saved in' i]",
    "div[role='main'] [role='button'][aria-label*='saved in' i]",
    "div[role='main'] button[aria-label*='add note' i]",
    "div[role='main'] [role='button'][aria-label*='add note' i]",
    # Fallback: button inside a group/region with "Saved in" aria-label
    "div[role='main'] [role='group'][aria-label*='Saved in' i] button",
    "div[role='main'] [role='region'][aria-label*='Saved in' i] button",
)
SAVE_CONTROL_CLICK_TIMEOUT_MS = 3000
SAVE_CONTROL_CLICK_MAX_ATTEMPTS = 3
SAVE_DIALOG_READY_TIMEOUT_MS = 5000
SAVE_DIALOG_CLOSE_TIMEOUT_MS = 3000
SAVE_DIALOG_LIST_RESOLVE_TIMEOUT_MS = 4500
NOTE_WRITE_MAX_ATTEMPTS = 2
NOTE_CONFIRM_TIMEOUT_MS = 4500
