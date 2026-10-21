# Organisations, members and roles

An organisation holds projects, members, a plan and an audit log. You may belong to several:
the organisation's name in the header switches between them, and starts a new one.

## Roles

| Role | May |
| --- | --- |
| Viewer | read every project the organisation lets them see, search, ask, and download |
| Member | as a viewer, and make projects, upload, organise, delete, export and choose who sees a project |
| Admin | as a member, and see every project, invite, change members' roles and remove them, choose the organisation's settings, make API keys and read the audit log |
| Owner | as an admin, and buy and change the plan, hand ownership over and delete the organisation |

Owners and admins change a role or remove a member on the [organisation page](/organization),
never one above their own. An organisation always has an owner: the last one hands ownership
over before leaving.

## Inviting

An owner or admin invites by email, with a role, under Invitations; the invitation lasts
$invitation_days days and can be sent again or revoked. They can also let everyone with a
confirmed address at their own email domain join, as a member or a viewer, under Joining by
email domain. Each member takes a place on the plan ([Plans and usage](/help/plans)).

## Private projects

"Who sees it" on a project's card makes it private: only the members chosen for it, and the
owners and admins, see it. Chats inside a project are private to whoever started them until
they share them.

## Settings and security

Owners and admins choose, on the organisation page, the language documents are searched in, to
keep chats for less time than the plan does, how members are told each kind of notice until
they choose themselves, to require two-factor sign-in of everyone, and to sign members in again
sooner. The audit log, under Security, says who did what and when.

## API keys

On a plan with API access, owners and admins make API keys for programs under API keys. A key
acts as the member who made it, in the organisation's projects alone, either reading or reading
and changing, for 30, 90 or 365 days or until revoked$api_rate. The key is shown once.
