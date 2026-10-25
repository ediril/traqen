"""The "Save to Traqen" bookmarklet."""

import json

MAX_NOTE = 2000  # longest text selection sent along as the note


def bookmarklet_code(base_url: str) -> str:
    """A `javascript:` URL that opens the capture popup for the current page."""
    capture = json.dumps(f"{base_url}/capture")
    script = (
        "(()=>{"
        f"const s=String(getSelection()).trim().slice(0,{MAX_NOTE});"
        f"const u={capture}+'?url='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title)"
        "+(s?'&note='+encodeURIComponent(s):'');"
        # Tell Traqen how it was opened, so after saving it closes the popup
        # or, in a tab (popup blocked), goes back to the page.
        "const w=open(u+'&popup=1','traqen','width=480,height=680');"
        "if(!w)location.href=u;"
        "})()"
    )
    return "javascript:" + script
