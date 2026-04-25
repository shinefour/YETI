---
name: YETI inbox concept and interaction model
description: The inbox is YETI's interpretation queue (not action queue). Clarifications + override actions, with structured answer schemas.
type: project
originSessionId: 64022ffc-735e-4cf5-8b51-2c9f6b05c915
---
The YETI inbox is **YETI's interpretation/clarification queue**, NOT an action execution queue. This is a critical conceptual distinction that shapes the whole flow.

**Why:** Daniel wants one place to clear quickly once or twice a day. Inbox items must be black-and-white clarifications, not open exploration. Actions (creating tasks, tickets, sending messages) must remain separate and explicit.

**How to apply:**

1. **Two distinct slots on every inbox item:**
   - **Clarification slot** — a concrete question + optional structured form schema (`answer_schema`). Daniel fills it; Claude interprets the answer and updates memory. That's it. No cascading actions.
   - **Override actions** — predefined buttons that take the item out of the inbox by *doing something* explicit: `Discard`, `Convert to task`, `Create Jira ticket`, etc. These are not answers, they're "stop asking, do this with it."

2. **Backend tells frontend what form to render** via `answer_schema`:
   ```json
   {
     "fields": [
       {"key": "full_name", "label": "Full name", "type": "text", "value": "Anni Mononen"},
       {"key": "role", "label": "Role", "type": "text", "value": "Program Manager"},
       {"key": "company", "label": "Company", "type": "text", "value": "Reaktor"}
     ]
   }
   ```
   Or for binary: `{"type": "choice", "options": ["yes", "no"]}`. Frontend renders any schema generically. If no schema, fall back to a single text box.

3. **Questions must be concrete and binary where possible.** Triage's job is to break vague things ("multiple team references need clarification") into concrete answerable questions, each with a schema.

4. **The line Claude must NOT cross:** Claude can update YETI's understanding (create derived KG facts, update person records, store new interpretations). Claude CANNOT affect anything outside YETI (create tasks, send messages, call external APIs) without an explicit button click from Daniel.

5. **Derived facts are encouraged.** When Daniel clarifies "Anni is a Program Manager at Reaktor, vendor for AA", Claude should create all derivable facts from that single answer (Anni→role→PM, Anni→works_at→Reaktor, Reaktor→vendor_of→AA, plus a contact record). The goal: the smarter the system gets, the less Daniel has to confirm.

6. **Hybrid UI principle:** for items where YETI has good guesses, show suggested choices as quick-pick chips. Always allow free text override. For items with no guesses, only the form/text. For image fallback, the structured form IS the value (acts as a checklist).

7. **Inbox is NOT chat.** They serve different human-interaction purposes — chat is conversation (you initiate), inbox is queue (YETI initiates). Even though the underlying mechanic (text in, Claude processes) is similar, the contexts must remain separate.

8. **Cost is acceptable.** Claude SHOULD be used for interpretation. The intelligence is the value.
