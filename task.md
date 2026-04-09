Building a Design Document for "YETI".

For my work I want to create a comprehensive AI centric system that would support my daily work. It should allow me to automate as much as possible and help me focus on the unique responsabilities and abilities and help me execute my projects and tasks.
Currently there are so many screens, portals, communications floating arround. I want to consolidate all in one.

Painpoints:
- Too many screens, portals, communications floating around — need to consolidate into one system
- Need a knowledge base for meeting notes
- Need a person network (name, contact, context about each person)
- Want to track certain topics over time — e.g. should I delegate a task? Does this fit my role? Are activities inline with project/company goals?

Confirmed Decisions:
- Messenger: Telegram
- Calendar: Microsoft 365 (Outlook)
- Chat platforms: Both Microsoft Teams and Slack
- Domain: Available (Daniel has one)
- Memory system: MemPalace over bare ChromaDB
- Web dashboard: On the same domain, for system status, knowledge browsing, and human-in-the-loop action item approval
- Knowledge base scope: All project/product documentation (not just meeting notes)

Optional Requirements and ideas:

The system should run in the cloud (maybe on Hetzner)
I should be able to integrate with and use Teams, Jira, Notion, Calendar, ...
Maybe some context system like https://github.com/milla-jovovich/mempalace could be helpful
I want to run this with multiple AI models (Claude, ChatGPT and for certain tasks a local model where it makes sense)
I want to be able to use my mobile phone (messenger) to interact with the system (obtain and enter information)
have the ability to send a picture that can be analyzed
it should have tools and agents that run in the background (or scheduled) to obtain and process information from different sources, generate a knowledge base and generate and manage action items. 
I want to be able to iterate on this system, not sure if this should be a context apart from the busines context.
security should be of concern. both in terms of the limits of the AI when using the external integrations and also of the knowledge base per sé.