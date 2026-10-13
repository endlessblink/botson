# Botson task routing

Read this checklist before planning or changing Botson code, content, schedules, or production state.

## Select the user's outcome

1. Read the latest user message and active goal. Extract the concrete deliverable, deadline, explicit order, and evidence needed to prove completion.
2. Apply precedence in this order: latest user correction; explicit order in the active goal; current user request; prior assistant plan; convenience or available coding work.
3. “Next” means the earliest unfinished outcome in the user's stated order. It does not mean the next backend task the agent can implement.
4. If the user corrects the selected task, stop that branch. Handle the corrected request first; do not defend the wrong work or present it as progress on the requested result.

## When the requested outcome is group activity

1. Read `config/operator_prefs.md`, `config/question_quality.md`, applicable current good/bad examples, and recent feedback.
2. Inspect the production schedule and identify the exact destination topic and local send window. Inspect recent available group context for repeats and topical fit. Never claim to have reviewed deleted or expired messages.
3. Select one specific, newcomer-friendly activity with a genuinely interesting group-relevant hook. Botson supplies the prompt or game; do not ask members to invent it, substitute generic small talk, force a joke, or post just to fill a schedule.
4. If the user asked to see a message before posting, present the exact proposed text and destination before sending. Wait for approval of that copy.
5. Do not start unrelated backend work before the immediate group-activity decision is completed. If production access blocks verification, prepare the candidate and identify the exact access gate.

## Report the requested result

Put the requested deliverable first in the response. Distinguish a drafted candidate, approved post, locally implemented change, deployed change, and production read-back. Never describe backend work as completing a live group action.
