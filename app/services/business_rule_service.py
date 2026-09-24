from app.db.file_repository import deactivate_business_rule, get_physical_file_by_id
from app.services.rule_suggestion_service import generate_rule_suggestions
from app.db.file_repository import (
    get_physical_file_by_id,
    save_business_rule,
    save_business_rule_answer,
)
from app.services.rule_suggestion_service import (
    generate_rule_suggestions,
)
from app.db.file_repository import (
    get_business_rule_answers,
    update_physical_file_status,
)
from app.db.file_repository import deactivate_business_rule
from app.db.file_repository import (
    get_business_rule_answer,
    get_rule_version,
    increment_rule_version,
)

def get_business_rule_questions(file_id: int) -> dict:
    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError(f"File {file_id} not found")

    status = physical_file[5]

    if status != "AWAITING_RULES":
        raise ValueError(
            f"Business rules cannot be configured. "
            f"Current file status: {status}"
        )

    suggestions = generate_rule_suggestions(file_id)

    return {
        "file_id": file_id,
        "status": status,
        "question_count": len(suggestions),
        "questions": suggestions,
    }

def convert_answer_to_rule_config(
    rule_type: str,
    answer: str,
) -> dict | None:

    answer = answer.upper()
    if rule_type == "DATA_TYPE":
        if answer == "NO":
            return None

        if answer in {"DECIMAL", "DATETIME"}:
            return {
                "type": answer
            }

        raise ValueError(
            f"Unsupported DATA_TYPE answer: {answer}"
        )

    if rule_type == "UNIQUE":
        if answer == "YES":
            return {"required": True}
        return None

    if rule_type == "NOT_NULL":
        if answer == "YES":
            return {"required": True}
        return None

    if rule_type == "ALLOW_NEGATIVE":
        return {
            "allow_negative": answer == "YES"
        }

    if rule_type == "VALID_DATETIME":
        if answer == "YES":
            return {"reject_invalid": True}
        return None

    if rule_type == "DUPLICATE_HANDLING":
        return {
            "strategy": answer
        }

    raise ValueError(
        f"Unsupported rule type: {rule_type}"
    )

def submit_business_rule_answers(
    file_id: int,
    answers: list,
) -> dict:

    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError(
            f"File {file_id} not found"
        )

    status = physical_file[5]

    if status != "AWAITING_RULES":
        raise ValueError(
            f"Business rules cannot be submitted. "
            f"Current file status: {status}"
        )

    suggestions = generate_rule_suggestions(file_id)

    valid_suggestions = {
        (
            suggestion["column_name"],
            suggestion["suggested_rule_type"],
        )
        for suggestion in suggestions
    }

    saved_rules = []
    skipped_answers = []

    # NEW:
    # Tracks whether this submission changes the effective
    # rule configuration.
    rules_changed = False

    for item in answers:

        key = (
            item.column_name,
            item.rule_type,
        )

        # Prevent clients from inventing arbitrary rules
        if key not in valid_suggestions:
            raise ValueError(
                f"Rule '{item.rule_type}' for column "
                f"'{item.column_name}' was not suggested"
            )

        # NEW:
        # Read the previous answer BEFORE overwriting it.
        existing_answer = get_business_rule_answer(
            file_id=file_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
        )

        # NEW:
        # New answer or changed answer means that the
        # rule configuration has changed.
        if existing_answer is None:
            rules_changed = True

        elif existing_answer[0] != item.answer:
            rules_changed = True

        # Save the user's answer for audit/history
        save_business_rule_answer(
            file_id=file_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
            answer=item.answer,
        )

        # Convert human answer into executable rule config
        config = convert_answer_to_rule_config(
            rule_type=item.rule_type,
            answer=item.answer,
        )

        if config is not None:

            saved_rule = save_business_rule(
                file_id=file_id,
                column_name=item.column_name,
                rule_type=item.rule_type,
                rule_config=config,
            )

            saved_rules.append(saved_rule)

        else:

            deactivate_business_rule(
                file_id=file_id,
                column_name=item.column_name,
                rule_type=item.rule_type,
            )

            skipped_answers.append({
                "column_name": item.column_name,
                "rule_type": item.rule_type,
                "answer": item.answer,
            })

    # NEW:
    # Increment ONCE for the entire configuration change,
    # not once for every changed answer.
    if rules_changed:
        rule_version = increment_rule_version(file_id)
    else:
        rule_version = get_rule_version(file_id)

    return {
        "file_id": file_id,
        "saved_rule_count": len(saved_rules),
        "skipped_answer_count": len(skipped_answers),
        "saved_rules": saved_rules,
        "skipped_answers": skipped_answers,

        # NEW
        "rules_changed": rules_changed,
        "rule_version": rule_version,
    }
def finalize_business_rules(file_id: int) -> dict:
    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError(
            f"File {file_id} not found"
        )

    status = physical_file[5]

    # Already finalized
    if status == "PROCESSING":
        return {
            "file_id": file_id,
            "finalized": True,
            "already_finalized": True,
            "status": "PROCESSING",
            "message": (
                "Business rules have already been finalized. "
                "Dataset is ready for Silver processing."
            ),
        }

# Invalid lifecycle state
    if status != "AWAITING_RULES":
        raise ValueError(
            f"Rules cannot be finalized. "
            f"Current file status: {status}"
        )
    suggestions = generate_rule_suggestions(file_id)
    answers = get_business_rule_answers(file_id)

    expected_questions = {
        (
            suggestion["column_name"],
            suggestion["suggested_rule_type"],
        )
        for suggestion in suggestions
    }

    answered_questions = {
        (
            answer[2],
            answer[3],
        ): answer[4]
        for answer in answers
    }

    missing_questions = []
    unresolved_questions = []

    for column_name, rule_type in expected_questions:
        answer = answered_questions.get(
            (column_name, rule_type)
        )

        if answer is None:
            missing_questions.append(
                {
                    "column_name": column_name,
                    "rule_type": rule_type,
                }
            )

        elif answer == "NOT_SURE":
            unresolved_questions.append(
                {
                    "column_name": column_name,
                    "rule_type": rule_type,
                }
            )

    if missing_questions or unresolved_questions:
        return {
            "file_id": file_id,
            "finalized": False,
            "missing_questions": missing_questions,
            "unresolved_questions": unresolved_questions,
        }

    update_physical_file_status(
        file_id=file_id,
        status="PROCESSING",
    )

    return {
        "file_id": file_id,
        "finalized": True,
        "status": "PROCESSING",
        "message": (
            "Business rules finalized. "
            "Dataset is ready for Silver processing."
        ),
    }

def update_business_rule_answer(
    file_id: int,
    item,
) -> dict:

    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError(
            f"File {file_id} not found"
        )

    status = physical_file[5]

    if status not in (
        "PROCESSING",
        "SUCCESS",
        "FAILED",
    ):
        raise ValueError(
            f"Business rules cannot be edited. "
            f"Current file status: {status}"
        )

    existing_answer = get_business_rule_answer(
        file_id=file_id,
        column_name=item.column_name,
        rule_type=item.rule_type,
    )

    if not existing_answer:
        raise ValueError(
            f"No existing rule answer found for "
            f"{item.column_name}:{item.rule_type}"
        )

    old_answer = existing_answer[0]

    # Idempotent edit
    if old_answer == item.answer:
        return {
            "file_id": file_id,
            "column_name": item.column_name,
            "rule_type": item.rule_type,
            "old_answer": old_answer,
            "new_answer": item.answer,
            "rules_changed": False,
            "rule_version": get_rule_version(file_id),
        }

    config = convert_answer_to_rule_config(
        rule_type=item.rule_type,
        answer=item.answer,
    )

    save_business_rule_answer(
        file_id=file_id,
        column_name=item.column_name,
        rule_type=item.rule_type,
        answer=item.answer,
    )

    if config is not None:

        save_business_rule(
            file_id=file_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
            rule_config=config,
        )

    else:

        deactivate_business_rule(
            file_id=file_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
        )

    new_rule_version = increment_rule_version(
        file_id
    )

    return {
        "file_id": file_id,
        "column_name": item.column_name,
        "rule_type": item.rule_type,
        "old_answer": old_answer,
        "new_answer": item.answer,
        "rules_changed": True,
        "rule_version": new_rule_version,
    }