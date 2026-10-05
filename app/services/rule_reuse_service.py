"""Reuse approved answers only within the existing exact schema version."""
from app.db.database import repository_transaction
from app.db.dataset_repository import get_dataset_version_file_by_id, get_rule_approval_context
from app.db.dataset_repository import initialize_dataset_version_file_rules as initialize_status
from app.db.file_repository import get_active_business_rules_for_dataset_version, get_business_rule_answers_for_dataset_version, get_dataset_profiles
from app.services.rule_suggestion_service import generate_rule_suggestions
from app.services.business_rule_service import convert_answer_to_rule_config


def approved_rules_cover_questions(approval, questions, answers, active_rules):
    if not approval or not approval[5] or approval[1] <= 0 or approval[2] != approval[1] or not active_rules:
        return False
    version = approval[0]
    if any(rule[1] != version for rule in active_rules) or any(answer[1] != version for answer in answers):
        return False
    saved = {(answer[2], answer[3]): answer[4] for answer in answers}
    active = {(rule[2], rule[3]): rule[4] for rule in active_rules}
    for question in questions:
        key = (question["column_name"], question["suggested_rule_type"])
        answer = saved.get(key)
        if answer not in question["options"]:
            return False
        expected = convert_answer_to_rule_config(key[1], answer)
        if (expected is None and key in active) or (expected is not None and active.get(key) != expected):
            return False
    return True


def initialize_dataset_version_file_rules(dataset_version_file_id: int):
    with repository_transaction():
        approval = get_rule_approval_context(dataset_version_file_id, lock=True)
        context = get_dataset_version_file_by_id(dataset_version_file_id)
        if context is None:
            raise ValueError("Processing context not found")
        if context[3] not in ("UPLOADED", "AWAITING_RULES"):
            # Existing association linkage/lifecycle is historical; never repin it.
            return context[:5]
        version = context[1]
        questions = generate_rule_suggestions(context[2])
        answers = get_business_rule_answers_for_dataset_version(version)
        active_rules = get_active_business_rules_for_dataset_version(version)
        columns = {profile[2] for profile in get_dataset_profiles(context[2])}
        eligible = approved_rules_cover_questions(approval, questions, answers, active_rules)
        eligible = eligible and all(rule[2] in columns for rule in active_rules)
        return initialize_status(dataset_version_file_id, approval[1] if eligible else None)
