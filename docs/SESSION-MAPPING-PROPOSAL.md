# Stable Lark destinations for Agent sessions

Date: 2026-09-13. Baseline: deployed 0.0.4. Status: proposal, not implemented or approved for rollout.

The user finds managing multiple sessions through successive cards in one DM inconvenient. Styling has not removed the hidden selected-session state or mixed output history.

## Recommendation

For a few ongoing sessions primarily used on mobile, give each Agent session a dedicated private Lark group containing the owner and the bot. Keep the existing bot DM for creating sessions, an overview and opening the corresponding group. A new prompt or task within an existing session must not create another group.

Opening a group selects the conversation by location. All input, output, questions, approvals and stop actions stay bound to that group's Agent session. No global /switch is needed. Switching Lark chats never interrupts another session.

The new group can attach to an existing native session without cloning or replaying tasks. Preserve old DM history and existing quoted-card targets; do not bulk-forward private history as part of migration. Test one newly created private group before migrating the existing bindings.

Progress should update a compact task card; final answers and actionable questions can add messages. Routine tool steps should not flood each group's timeline. Ending a task leaves the group reusable; archiving an Agent session preserves chat history and stops routine progress publication. Do not claim a Lark API for automatically archiving or pinning conversations.

## Alternatives

| Mapping | Suitable use | Tradeoff |
|---|---|---|
| One private group per Agent session | A few ongoing sessions, familiar mobile chat navigation | Many short-lived sessions create many groups |
| One project group, one thread per Agent session | Many related sessions under a project | Requires entering the correct thread; mobile discovery and notifications need validation |
| A web session workspace inside Lark | Many sessions, search/filter/split view, input completion | A separate UI, hosting and authentication; notifications can remain in Lark |

Thread mode is a valid longer-term alternative, not merely quoting old messages in the existing DM. Use a normal group with group_message_type=thread; the API's older chat_mode=topic is a different entity. A top-level topic maps to a session; replies in that thread continue it. A stable topic entry contains title/status, not every intermediate output.

If the user strongly prefers keeping only one Lark chat, prioritize the web workspace over multiplying navigational cards in that same message stream. A bot menu can open the workspace but does not itself separate chat histories.

## Verified platform facts

Read official Lark documentation on 2026-09-13, after checking the shared SDK wiki. No group was created, no app permissions were changed and no messages were sent during this proposal.

- Chat creation supports private groups, invited user IDs, bot auto-membership and group_message_type=chat/thread. Creation needs im:chat:create or im:chat.
- Message events carry chat_id and optional thread_id/root_id. Thread reply is supported by reply_in_thread; supported group mode must be checked.
- Group messages without an explicit bot mention require an appropriate all-group-messages scope. Mention-only scopes cannot deliver that UX. Tenant permission approval remains an implementation prerequisite, not assumed granted.
- The installed Channel SDK already normalizes threads and exposes group allowlists/mention policy. Unilark0.0.4 deliberately disables groups and currently binds the owner's identity to a DM, so this is a routing/authorization change, not a configuration toggle.

## Implementation boundaries

- Keep authenticated owner identity separate from allowed chat destinations. Bind each destination to exactly one intended native session. For thread mode, use chat plus thread identity, not a mutable global selection.
- Owner-only private groups must not become general collaboration groups implicitly. Verify membership and enforce a policy for unexpected membership changes; reject unauthorized input and suspend private output rather than exposing task history.
- Persist group/thread provisioning intent before external API calls. Reconcile ambiguous creation results rather than blindly retrying; documented creation UUID deduplication lasts10hours and is not permanent idempotency.
- Callback authorization still checks owner, actual card message, original session and current permission/stop fingerprint. Opening a different group cannot retarget an old button.
- Validate native input/output routing in two different groups, original-session approval/stop, restart recovery and duplicate delivery. Then test unmentioned input, mobile navigation, notifications and Markdown/Mermaid in the real client.
- Better multi-session navigation does not establish concurrent execution. Current execution limit remains1 until independently implemented and verified with overlapping native sessions.

## Sources

- [Create chat](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/chat/create.md)
- [Thread introduction](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/thread-introduction.md)
- [Reply in thread](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/reply.md)
- [Receive message permissions and identity](https://open.larksuite.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/events/receive.md)
- [Lark application forms](https://open.larksuite.com/llms.txt)
- [Web application introduction](https://open.larksuite.com/document/client-docs/h5/introduction.md)
