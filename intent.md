# Intent: Monitoring dashboard for headless LLM build hosts based on ubuntu Linux

Author: Matt Ashby. Status: draft.

## Opportunity

Build a monitoring dashboard for the LLM build host agents-home on the local network. The dashboard will run on a docker container on this machine.

## Proposal

I need the monitoring dashboard to visualise resource consumption on agents-home, running processes, package and OS update availability status, let me trigger the update process for package or OS updates from the dashboard. agents-home is headless so give me a view of the screen that is refreshed every 30 seconds or on demand. Base the layout on the Windows Admin Center layout and capabilities.

## Objective

Build a monitoring dashboard for agents-home.

## Constraints

The dashboard needs to run in a docker container on this local windows machine.

## Decisions

- Screen view uses tmux session capture, not a virtual framebuffer or VNC. agents-home is headless and the work to watch (e.g. OS updates) runs in a terminal, not a desktop. Long-running jobs run inside tmux so they survive an SSH disconnect, and the dashboard shows a read-only snapshot (`tmux capture-pane -p`) refreshed every 30 seconds or on demand. Updates triggered from the dashboard run in tmux too.
- No agent is installed on agents-home. The container connects over SSH as `agents` and runs a stdlib Python collector each poll.
- Authentication is a one-time unlock at start-up: the key passphrase loads the key into an ssh-agent inside the container. The dashboard has no login of its own, so it listens on 127.0.0.1 only.

## Open questions
