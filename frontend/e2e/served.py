"""The app as the browser test serves it (e2e/serve.sh): app.main's, with a feature flag of the
test's own in the registry (app/flags.py), which names none of its own until the first feature
behind one is built, so that the test turns a flag on for an organisation from the operator's
area and reads the row its audit log keeps of it."""

from app.flags import FLAGS, Flag

FLAGS["e2e_preview"] = Flag("A preview the browser test turns on", share=0)

from app.main import app  # noqa: E402

__all__ = ["app"]
