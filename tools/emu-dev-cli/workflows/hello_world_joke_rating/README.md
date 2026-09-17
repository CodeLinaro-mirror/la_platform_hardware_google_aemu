# Hello World Joke Rating Workflow

An automated workflow that reviews an existing joke file, writes a one-paragraph summary of the pros and cons of the joke, and assigns a numerical rating on a 10-point scale.

## Specification

- **Name:** `hello-world-joke-rating`
- **Arguments:**
  - `joke-file` (required): Path to the joke file to review.
- **Output:**
  - `{rating-file}`: Markdown file created in the workflow cache directory (`~/.cache/emu-dev-cli/workflows/hello-world-joke-rating/joke-rating-<state_id>.md`).
- **Verifier:** `verify_joke_rating.py` ensures the summary contains at least 15 words discussing pros/cons and a valid rating matching `Rating: x.x / 10`.

## Usage

```bash
# Initialize a new rating session
emu-dev-cli workflow hello-world-joke-rating init /path/to/joke.txt

# Run subsequent steps
emu-dev-cli workflow hello-world-joke-rating --state=<state_id>
```
