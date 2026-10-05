from app.db.file_repository import (
    get_dataset_profiles,
    get_physical_file_by_id,
    get_business_rule_answers_for_dataset_version,
    get_active_business_rules_for_dataset_version,
    get_business_rule_answer_for_dataset_version,
    save_business_rule_answer_for_dataset_version,
    save_business_rule_for_dataset_version,
    deactivate_business_rule_for_dataset_version,
)

from app.db.dataset_repository import (
    get_dataset_version_file_by_id,
    update_dataset_version_file_status,
    get_dataset_version_rule_version,
    increment_dataset_version_rule_version,
    get_rule_approval_context,
    approve_association_rules,
)

from app.services.rule_suggestion_service import (
    generate_rule_suggestions,
)

def get_business_rule_questions(
    dataset_version_file_id: int,
) -> dict:

    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    physical_file = get_physical_file_by_id(file_id)

    if not physical_file:
        raise ValueError(
            f"Physical file {file_id} not found"
        )

    # Suggestions are generated from the physical file's
    # Bronze profile. This is safe to reuse because the
    # underlying physical data is identical.
    suggestions = generate_rule_suggestions(file_id)

    approval = get_rule_approval_context(dataset_version_file_id)

    return {
        "dataset_version_file_id": dataset_version_file_id,
        "dataset_version_id": dataset_version_id,
        "file_id": file_id,
        "status": status,
        "question_count": len(suggestions),
        "questions": suggestions,
        "rule_version": get_dataset_version_rule_version(dataset_version_id),
        "answers": [
            {"column_name": answer[2], "rule_type": answer[3], "answer": answer[4]}
            for answer in get_business_rule_answers_for_dataset_version(dataset_version_id)
        ],
        "active_rule_count": len(get_active_business_rules_for_dataset_version(dataset_version_id)),
        "rules_reused": approval[4],
        "applied_rule_version": approval[3],
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
    dataset_version_file_id: int,
    answers: list,
) -> dict:

    # Resolve the logical processing context.
    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    if status != "AWAITING_RULES":
        raise ValueError(
            "Business rules cannot be submitted. "
            f"Current dataset-version-file status: {status}"
        )

    # Suggestions are derived from the shared physical
    # file / Bronze profile.
    suggestions = generate_rule_suggestions(file_id)

    valid_suggestions = {
        (
            suggestion["column_name"],
            suggestion["suggested_rule_type"],
        ): suggestion["options"]
        for suggestion in suggestions
    }

    # Validate the entire submission before storing any answer. An option valid
    # for another question type must not be silently interpreted as NO.
    seen = set()
    for item in answers:
        key = (item.column_name, item.rule_type)
        if key in seen or item.answer.upper() not in valid_suggestions.get(key, []):
            raise ValueError("Invalid or duplicate rule answer")
        seen.add(key)

    saved_rules = []
    skipped_answers = []
    rules_changed = False

    for item in answers:

        key = (
            item.column_name,
            item.rule_type,
        )

        # Clients may only answer rules suggested
        # by the profiling/suggestion engine.
        if key not in valid_suggestions:
            raise ValueError(
                f"Rule '{item.rule_type}' for column "
                f"'{item.column_name}' was not suggested"
            )

        normalized_answer = item.answer.upper()

        # IMPORTANT:
        # Previous answer is scoped to DatasetVersion,
        # not the shared physical file.
        existing_answer = (
            get_business_rule_answer_for_dataset_version(
                dataset_version_id=dataset_version_id,
                column_name=item.column_name,
                rule_type=item.rule_type,
            )
        )

        if existing_answer is None:
            rules_changed = True

        elif existing_answer[0] != normalized_answer:
            rules_changed = True

        # Store the human/business answer under
        # this logical dataset version.
        save_business_rule_answer_for_dataset_version(
            dataset_version_id=dataset_version_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
            answer=normalized_answer,
        )

        config = convert_answer_to_rule_config(
            rule_type=item.rule_type,
            answer=normalized_answer,
        )

        if config is not None:

            saved_rule = (
                save_business_rule_for_dataset_version(
                    dataset_version_id=dataset_version_id,
                    column_name=item.column_name,
                    rule_type=item.rule_type,
                    rule_config=config,
                )
            )

            saved_rules.append(saved_rule)

        else:

            deactivate_business_rule_for_dataset_version(
                dataset_version_id=dataset_version_id,
                column_name=item.column_name,
                rule_type=item.rule_type,
            )

            skipped_answers.append({
                "column_name": item.column_name,
                "rule_type": item.rule_type,
                "answer": normalized_answer,
            })

    # Increment once per changed submission.
    if rules_changed:
        rule_version = (
            increment_dataset_version_rule_version(
                dataset_version_id
            )
        )
    else:
        rule_version = (
            get_dataset_version_rule_version(
                dataset_version_id
            )
        )

    return {
        "dataset_version_file_id": dataset_version_file_id,
        "dataset_version_id": dataset_version_id,
        "file_id": file_id,
        "saved_rule_count": len(saved_rules),
        "skipped_answer_count": len(skipped_answers),
        "saved_rules": saved_rules,
        "skipped_answers": skipped_answers,
        "rules_changed": rules_changed,
        "rule_version": rule_version,
    }
def finalize_business_rules(
    dataset_version_file_id: int,
) -> dict:

    # Resolve the logical dataset context.
    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    # Idempotency: finalizing twice should be safe.
    if status == "READY_FOR_SILVER":
        approval = get_rule_approval_context(dataset_version_file_id)
        if approval[3] is not None and approval[3] != approval[1]:
            raise ValueError("The association uses a historical rule version")
        if approval[3] is None:
            approve_association_rules(dataset_version_file_id)
        return {
            "dataset_version_file_id": dataset_version_file_id,
            "dataset_version_id": dataset_version_id,
            "file_id": file_id,
            "finalized": True,
            "already_finalized": True,
            "status": status,
            "message": (
                "Business rules have already been finalized. "
                "Dataset is ready for Silver processing."
            ),
        }

    if status != "AWAITING_RULES":
        raise ValueError(
            "Rules cannot be finalized. "
            f"Current status: {status}"
        )

    # Suggestions depend on the physical data/profile.
    suggestions = generate_rule_suggestions(file_id)

    # Answers belong to the logical dataset version.
    answers = get_business_rule_answers_for_dataset_version(
        dataset_version_id
    )

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
            missing_questions.append({
                "column_name": column_name,
                "rule_type": rule_type,
            })

        elif answer == "NOT_SURE":
            unresolved_questions.append({
                "column_name": column_name,
                "rule_type": rule_type,
            })

    if missing_questions or unresolved_questions:
        return {
            "dataset_version_file_id": dataset_version_file_id,
            "dataset_version_id": dataset_version_id,
            "file_id": file_id,
            "finalized": False,
            "status": status,
            "missing_questions": missing_questions,
            "unresolved_questions": unresolved_questions,
        }

    if not get_active_business_rules_for_dataset_version(dataset_version_id):
        return {
            "dataset_version_file_id": dataset_version_file_id,
            "dataset_version_id": dataset_version_id,
            "file_id": file_id,
            "finalized": False,
            "status": status,
            "message": "Silver requires at least one active business rule. Review your answers; no rules were activated.",
        }

    updated_context = approve_association_rules(dataset_version_file_id)

    return {
        "dataset_version_file_id": dataset_version_file_id,
        "dataset_version_id": dataset_version_id,
        "file_id": file_id,
        "finalized": True,
        "already_finalized": False,
        "status": updated_context[3],
        "message": (
            "Business rules finalized. "
            "Dataset is ready for Silver processing."
        ),
    }
def update_business_rule_answer(
    dataset_version_file_id: int,
    item,
) -> dict:

    # Resolve logical processing context.
    context = get_dataset_version_file_by_id(
        dataset_version_file_id
    )

    if context is None:
        raise ValueError(
            f"Dataset-version-file "
            f"{dataset_version_file_id} not found"
        )

    dataset_version_id = context[1]
    file_id = context[2]
    status = context[3]

    # Rules may only be edited after the initial rule configuration
    # has been finalized / processing has progressed.
    if status not in (
        "READY_FOR_SILVER",
        "PROCESSING",
        "SUCCESS",
        "FAILED",
    ):
        raise ValueError(
            "Business rules cannot be edited. "
            f"Current dataset-version-file status: {status}"
        )

    existing_answer = (
        get_business_rule_answer_for_dataset_version(
            dataset_version_id=dataset_version_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
        )
    )

    if not existing_answer:
        raise ValueError(
            f"No existing rule answer found for "
            f"{item.column_name}:{item.rule_type}"
        )

    old_answer = existing_answer[0]

    # Idempotent edit:
    # same answer means no new rule version.
    if old_answer == item.answer:
        return {
            "dataset_version_file_id": dataset_version_file_id,
            "dataset_version_id": dataset_version_id,
            "file_id": file_id,
            "column_name": item.column_name,
            "rule_type": item.rule_type,
            "old_answer": old_answer,
            "new_answer": item.answer,
            "rules_changed": False,
            "rule_version": (
                get_dataset_version_rule_version(
                    dataset_version_id
                )
            ),
        }

    config = convert_answer_to_rule_config(
        rule_type=item.rule_type,
        answer=item.answer,
    )

    save_business_rule_answer_for_dataset_version(
        dataset_version_id=dataset_version_id,
        column_name=item.column_name,
        rule_type=item.rule_type,
        answer=item.answer,
    )

    if config is not None:
        save_business_rule_for_dataset_version(
            dataset_version_id=dataset_version_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
            rule_config=config,
        )
    else:
        deactivate_business_rule_for_dataset_version(
            dataset_version_id=dataset_version_id,
            column_name=item.column_name,
            rule_type=item.rule_type,
        )

    # A changed effective configuration creates a new logical
    # rule version for this dataset version only.
    new_rule_version = (
        increment_dataset_version_rule_version(
            dataset_version_id
        )
    )

    # Previous Silver/Gold runs remain historical records.
    # This DVF must now be processed again using the new rules.
    update_dataset_version_file_status(
        dataset_version_file_id=dataset_version_file_id,
        status="READY_FOR_SILVER",
    )

    return {
        "dataset_version_file_id": dataset_version_file_id,
        "dataset_version_id": dataset_version_id,
        "file_id": file_id,
        "column_name": item.column_name,
        "rule_type": item.rule_type,
        "old_answer": old_answer,
        "new_answer": item.answer,
        "rules_changed": True,
        "rule_version": new_rule_version,
        "status": "READY_FOR_SILVER",
    }
