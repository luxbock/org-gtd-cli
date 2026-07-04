## Tags

### Context tags (mutually exclusive - a task has at most one)

- `@errand` - requires going out
- `@home` - can only be done at home
- `@phone` - requires a phone call
- `@computer` - task done on the computer
- `@agent` - this specific task can be completed autonomously by an AI agent without human involvement. Not "related to agents." Only tag individual leaf tasks, never parent headings (tags inherit in org-mode).

### Functional tags

- `buy` - something to purchase
- `email` - requires sending email
- `url` - web link to check out
- `note` - reference note, not a task
- `idea` - idea to explore
- `nocal` - exclude from calendar views
- `calpersonal` - personal calendar event (used in `calendar.org`)

### Category tags meaning

The following is useful background information about how the following tags are used:

- `nixos` - takes place in the `~/nixos-config` repository
- `org_gtd` - takes place in `~/org` repository
- `org` - is a sub-category of `nixos`
- `emacs` - is a sub-category of `nixos`
- `epiphyte` - my homelab server, often a subcategory of `nixos`, though not always
- `kinakuta` - my laptop, main workhorse
- `family` - tasks related to my house or family members
- `finance` - investments, property, payments, money transfers, debts

### Discovering tags in use

The lists above are canonical, not exhaustive. Before adding any tag that is not listed above, run `org-gtd-cli --json list-tags` — it returns every tag currently in use with its usage count (literal occurrences, no inheritance), sorted most-used first. Prefer reusing an existing tag over coining a new near-duplicate (e.g. reuse `computers`, don't invent `pcs`).

Tags go at the end of the headline, colon-delimited: `* TODO Buy groceries :buy:@errand:`

Tags on parent headings are inherited by children - do not repeat them on subtasks.

Tags cannot contain dashes — use underscores instead (e.g. `claude_harness`, not `claude-harness`). A dashed tag is silently dropped: it ends up as text in the heading and `tags` stays empty.
