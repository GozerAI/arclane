# Post-Intake Operating Model

After intake, Arclane should stop feeling speculative and start behaving like an operating system.

## 1. Persist an operating plan

Every new business gets a stored `operating_plan` in `Business.agent_config` with:

- `agent_tasks`: the first specialist tasks the orchestrator will run
- `user_recommendations`: high-leverage approval or steering actions for the user
- `provisioning`: subdomain, mailbox, public URL, workspace path, and step-by-step provisioning state
- `code_storage`: where the tenant code lives and how Arclane tracks it

This keeps the launch logic inspectable instead of rebuilding it from scratch every cycle.

## 2. Split execution into two rails

- Agent rail: strategy, market research, content, and operations tasks
- Provisioning rail: subdomain, mailbox, workspace staging, and deployment

The user should see both rails moving. Agent output proves business work is happening. Provisioning proves the business is becoming real infrastructure.

## 3. Store code in tenant workspaces

Arclane stores generated business code under the configured workspace root:

- default: `/var/arclane/workspaces/<slug>`
- source: copied from the selected template
- manifest: `arclane-workspace.json`

The manifest is the minimum operational contract for code storage. It records slug, template, subdomain, workspace path, storage mode, and allocated port.

## 4. Deploy as isolated tenant surfaces

Deployment remains Docker-per-tenant behind Caddy:

- Caddy reserves and routes `<slug>.<domain>`
- Resend powers mailbox identity
- Docker builds and runs the isolated tenant workspace
- Caddy switches the route to the live upstream after deployment

## 5. Show the work, not hidden reasoning

The dashboard should expose:

- an ops stream for live execution movement
- visible deliverables
- the launch queue and recommendations
- provisioning state and code/workspace status

This keeps Arclane operator-focused without exposing real chain-of-thought.
