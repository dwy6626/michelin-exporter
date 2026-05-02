"""Selector and selector-adjacent constants for Google Maps driver."""

GOOGLE_SESSION_COOKIE_NAMES = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "SIDCC",
        "__Secure-1PSID",
        "__Secure-3PSID",
    }
)

SEARCH_BOX_SELECTORS = (
    "input#searchboxinput",
    "div[role='search'] form input[name='q'][role='combobox']",
    "div[role='search'] form input[name='q']",
    "form.NhWQq input[name='q'][role='combobox']",
    "form.NhWQq input[name='q']",
    "input[name='q'][role='combobox']",
)
SEARCH_BUTTON_SELECTOR = "button#searchbox-searchbutton"
SIGNED_IN_ANCHOR_SELECTORS = (
    "button[aria-label*='Google Account']",
    "a[aria-label*='Google Account']",
    "button[aria-label*='Account']",
)
SIGN_IN_BUTTON_SELECTORS = (
    "a[aria-label*='Sign in']",
    "button[aria-label*='Sign in']",
    "a[href*='accounts.google.com']",
)
FIRST_RESULT_SELECTORS = (
    "div[role='article'] a",
    "a[href*='/maps/place/']",
    "div[role='feed'] a",
    "div[role='article'] button",
)
PLACE_TITLE_SELECTORS = (
    "h1",
    "h1.fontHeadlineLarge",
)
PLACE_SUBTITLE_SELECTORS = (
    "h2.bwoZTb",
    "h1.DUwDvf + h2",
)
PLACE_ADDRESS_SELECTORS = (
    "button[data-item-id='address']",
    "button[aria-label^='Address']",
)
PLACE_CATEGORY_SELECTORS = (
    "button[jsaction*='category']",
    "button[jsaction*='pane.rating.category']",
    "button[aria-label*='Category']",
)
PLACE_LOCATED_IN_SELECTORS = (
    "button[data-item-id='locatedin']",
    "button[aria-label^='Located in:']",
)
ADD_PLACE_INPUT_SELECTORS = (
    "input[aria-label*='Search for a place to add']",
    "input[aria-label*='place to add' i]",
)
INLINE_LIST_TITLE_BUTTON_SELECTORS = (
    "h1 button",
    "button.M77dve",
    "button[jsaction*='pane.wfvdle219']",
    "button[jsaction*='savedPlaceList.title']",
)
INLINE_LIST_NAME_INPUT_SELECTORS = (
    "h1 input[type='text']",
    "h1 [role='textbox']",
    "h1 [contenteditable='true']",
    "div.miFGmb input[jsaction*='textEntry.input'][maxlength='40']",
    "div.miFGmb input[type='text'][maxlength='40']",
    "div.miFGmb input[type='text']",
    "button.M77dve input[type='text']",
    "button.M77dve [role='textbox']",
    "button[jsaction*='pane.wfvdle219'] input[type='text']",
    "button[jsaction*='pane.wfvdle219'] [role='textbox']",
    "input[jsaction*='wfvdle219']",
    "input[jsaction*='textEntry.input'][maxlength='40']",
    "input.Tpthec.fontTitleLarge",
    "[contenteditable='true'][jsaction*='wfvdle219']",
    "input[aria-label*='list' i]:not(#searchboxinput)",
    "textarea[aria-label*='list' i]:not(#searchboxinput)",
    "[role='textbox'][aria-label*='list' i]",
    "[contenteditable='true'][aria-label*='list' i]",
)
LIST_NAME_INPUT_EXCLUSION_PHRASES = (
    "search for a place to add",
    "place to add",
    "add a place",
    "add place",
    "list description",
    "description",
    "wfvdle221",
    "wfvdle220",
    "omnibox",
    "combobox",
    "searchboxinput",
)
