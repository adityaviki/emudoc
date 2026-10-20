"""Count a document without pages by its text.

Revision 67eb6d3cb16c, written 2026-10-20.

The pages a plan allows a month were counted from `page_count`, which only a PDF and an image
have, so a Word file, a sheet or a text file was indexed against no allowance. The first parse
now counts such a document by its text (`text_pages` in app/entitlements.py), and tells that
its pages were counted by the document's usage (`_pages_counted` in app/indexing/pipeline.py)
rather than by `page_count`, which such a document never has. A document parsed before was
counted by the old rule, nothing for one without pages, and "Index again" brings nothing new
in, so each is marked counted for 0 pages where its usage holds no count: a count of 0 is one
`record` never writes (app/usage.py), shown by no page, and the month's events are unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "67eb6d3cb16c"
down_revision: str | None = "920efab20e8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A parse that ended once left a page count, a parse stage done or the blocks it found.
    op.execute(
        """
        UPDATE documents
        SET usage = jsonb_set(
            usage, '{upload}', coalesce(usage -> 'upload', '{}'::jsonb) || '{"pages": 0}'::jsonb
        )
        WHERE usage #> '{upload,pages}' IS NULL
          AND (
            page_count IS NOT NULL
            OR stages #>> '{parse,status}' = 'done'
            OR EXISTS (SELECT FROM blocks WHERE blocks.document_id = documents.id)
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE documents SET usage = usage #- '{upload,pages}'
        WHERE usage #>> '{upload,pages}' = '0'
        """
    )
    op.execute("UPDATE documents SET usage = usage - 'upload' WHERE usage -> 'upload' = '{}'")
