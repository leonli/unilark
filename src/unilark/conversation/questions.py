"""Translate explicit card choices or quoted text into typed question answers."""

from unilark.adapters.sidecars.views import Answer, Question


def parse(questions: tuple[Question, ...], text: str, decision: str = "text") -> tuple[Answer, ...]:
    if decision.startswith("answer:"):
        chosen = decision.removeprefix("answer:")
        if len(questions) != 1 or questions[0].multi or chosen not in dict(questions[0].options):
            raise ValueError("选项已变化，请查看当前问题。")
        return (Answer((chosen,)),)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(questions) == 1:
        lines = [text.strip()]
    if len(lines) != len(questions):
        raise ValueError("请按问题顺序每题回复一行；多选项用逗号分隔。")
    answers = []
    for question, line in zip(questions, lines, strict=True):
        labels = {label: identity for identity, label in question.options}
        if len(labels) != len(question.options):
            answers.append(Answer(text=line))
            continue
        parts = (
            [p.strip() for p in line.replace("，", ",").split(",")] if question.multi else [line]
        )
        if parts and all(p in labels for p in parts):
            answers.append(Answer(tuple(dict.fromkeys(labels[p] for p in parts))))
        elif line:
            answers.append(Answer(text=line))
        else:
            raise ValueError("回答不能为空。")
    return tuple(answers)
