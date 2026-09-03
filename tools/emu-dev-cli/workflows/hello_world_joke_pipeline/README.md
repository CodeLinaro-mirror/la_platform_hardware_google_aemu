# Hello World Joke Pipeline Workflow

A composite workflow demonstrating declarative sub-workflow orchestration.

## Execution Flow

1. **Step 0: Generate Joke**:
   Delegates to `hello-world-example`. Prompts the user to write their name and a joke. Captures `hello-file` output as `joke_file`.
2. **Step 1: Rate Joke**:
   Delegates to `hello-world-joke-rating`. Passes `{hello-world-example.joke_file}` as the `joke-file` input argument using dot notation. Prompts for a pros/cons review and rating. Captures `rating-file` as `final_rating` nested under `hello-world-joke-rating`.
3. **Step 2: Completion**:
   Summarizes the finished pipeline with direct references to `{hello-world-example.joke_file}` and `{hello-world-joke-rating.final_rating}`.

## Single State ID Guarantee

Developers and AI agents only interact with the top-level pipeline session:
```bash
emu-dev-cli workflow hello-world-joke-pipeline init
emu-dev-cli workflow hello-world-joke-pipeline --state=<pipeline_state_id>
```
