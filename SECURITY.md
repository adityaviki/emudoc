# Security

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through GitHub:
on the repository's **Security** tab, choose **Report a vulnerability**. Only the maintainers
see the report, and you follow and discuss it there with them.

Say what you found and how to reproduce it: the version (the user menu and `/api/health` name
it), the settings that matter, the requests or the document that show it, and what someone could
do with it. A finding from an automated scanner is welcome once you have checked that it applies
to Emudoc as it runs.

The maintainers confirm the problem in the advisory, fix it and release a new version. The
advisory is then published with the version that fixes it, crediting you unless you would rather
not be named. Please keep the problem private until then.

## Supported versions

Fixes go into the latest release. While Emudoc is at 0.x, a fix comes as a new release of the
latest minor version, and older ones are not patched: upgrade to the latest (`deploying.md`,
"Upgrades").

## What counts

Emudoc runs on a team's own server, so part of its security is the team's: the server, the
reverse proxy and its certificate, who reaches the database and the storage, the backups, the
model providers it sends documents to, and its settings. `deploying.md` says what each needs. A
weakness of that setup is not a vulnerability in Emudoc, unless Emudoc's defaults or its guide
lead to it.

These are, for example:

- reaching what one should not: another member's private project, a chat not shared, someone
  else's My files, a document through a share link that was revoked or has expired;
- doing more than one's role allows (a viewer changing a project, a member doing an admin's
  work), or acting as someone else;
- an upload, a document's content, a webhook's address or a connected folder that makes the
  server run code, read its own files, or reach addresses it should not;
- a document whose planted instructions get the agent to show or change what the person asking
  could not reach themselves;
- a secret or a key ending up in a log, a response, an export or a backup in the clear.

A problem in one of Emudoc's dependencies goes to that project; tell us as well when Emudoc uses
it in a way that can be exploited.
