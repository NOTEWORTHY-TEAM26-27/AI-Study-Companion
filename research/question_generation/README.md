# Question generation (Sprint 2)

This prototype generates study questions from a lecture PDF in four formats, saves them in one
shared JSON format, and checks the output. It uses the local `llama3.2:3b` model through Ollama.

## Files

- `sprint2_question_generator.py`: reads the PDF text and generates 20 questions.
- `validate_questions.py`: checks that every question has the required fields for its type.

## How to run

From the repository root, with the virtual environment active:

```bash
pip install pypdf
ollama pull llama3.2:3b
python research/question_generation/sprint2_question_generator.py path/to/lecture.pdf
python research/question_generation/validate_questions.py path/to/lecture.json
```

The generator saves the questions next to the PDF with the same name and a `.json` extension.

If Ollama is not running, or the model's questions fail the checks (wrong fields, or a source
excerpt that is not copied exactly from the PDF), the script fills the set from a PDF fallback
that builds questions straight from the PDF text without the model. It prints how many
questions came from each.

## Question format

Each PDF gets 20 questions:

| Question type | Questions per PDF |
|---|---:|
| Multiple choice | 5 |
| True/false | 5 |
| Fill in the blank | 5 |
| Short answer | 5 |

Every question has the same six fields: `type`, `question`, `choices`, `answer`,
`source_page`, and `source_excerpt`.

## Results

Tested on three lecture PDFs from different courses:

| PDF | Questions | Format check | Source page matches |
|---|---:|---|---|
| fuzzing.pdf | 20 | Passed | Matched |
| An Introduction to Design Patterns.pdf | 20 | Passed | Matched |
| cse4308-sp26-lec19.pdf | 20 | Passed | Matched |
| Total | 60 | 60/60 passed | 60/60 matched |

## Next steps

- Check the questions for factual accuracy and quality, not only format.
- Try a larger model (Claude Haiku 5.5 on DigitalOcean) and compare.
- Move the generator into the app behind the shared model client (12-5, Sprint 3).
