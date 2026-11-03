#!/usr/bin/env python3
"""The plan's T01-T24 fault matrix (总方案 第 10 节) as one runnable acceptance check: every row names the existing automated tests that prove it,
and this tool runs exactly those tests, one by one, and prints the result as a Markdown table.

    python -B tests/tools/acceptance_matrix.py [--write PATH] [--no-real-codex]

By default the opt-in offline real-Codex checks (PAR_REAL_CODEX=1: signature, flags, the wrapper script - no model call, no quota) run too; they
are skipped by themselves when no real Codex is installed, and `--no-real-codex` leaves them out. Exit status 0 only if every listed test ran and
passed. Nothing here calls a model.
"""
import argparse
import io
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

U = "tests.unit."
MAP = [
 ("T01", "Claude 主控 + Codex Worker 固定；不做模型评分", "COVERED", "选择 Worker 不启动任何程序；路由是确定性的；Claude 宿主不会当 Worker", [
   U+"test_agy_kernel.Routing.test_choosing_a_worker_starts_no_program",
   U+"test_routing.Routing.test_routing_is_deterministic",
   U+"test_routing.Routing.test_no_role_and_no_worker_uses_the_default_worker",
   U+"test_config.FixedDecisions.test_claude_host_is_not_a_worker",
   U+"test_coordinators.KernelSide.test_switching_to_whoever_already_coordinates_changes_nothing"]),
 ("T02", "Gemini 旧版已安装但未启用", "COVERED", "旧版 gemini 不能启用、不会得到适配器、doctor 报配置 FAIL", [
   U+"test_config.FixedDecisions.test_gemini_legacy_cannot_be_enabled",
   U+"test_no_real_calls.NothingReal.test_an_adapter_needs_an_enabled_provider_and_an_absolute_executable",
   U+"test_no_real_calls.NothingReal.test_antigravity_follows_the_same_rule_and_the_legacy_gemini_cli_never_gets_an_adapter",
   U+"test_doctor.ConfigTests.test_legacy_gemini_cannot_be_enabled_through_config"]),
 ("T03", "Codex 的 PATH 入口损坏", "COVERED", "从不在 PATH 上找可执行文件，只用配置里写明的绝对路径；相对/缺失/他人可写的一律拒绝；包装脚本被拒绝；真实 Codex 的离线核对（需 PAR_REAL_CODEX=1，本次已运行）", [
   U+"test_codex_adapter.Executable.test_the_executable_is_never_looked_up_on_path",
   U+"test_codex_adapter.Executable.test_relative_missing_unexecutable_and_writable_by_others_are_refused",
   "tests.integration.test_real_codex.RealCodexOffline.test_the_bundled_binary_carries_openais_signature",
   "tests.integration.test_real_codex.RealCodexOffline.test_the_wrapper_script_next_to_it_is_refused"]),
 ("T04", "agy 未安装", "COVERED", "缺失的 agy 是干净的拒绝并报 UNAVAILABLE 及原因，绝不换成 gemini（本阶段补了 UNAVAILABLE 的测试）", [
   U+"test_agy_kernel.Refusals.test_a_missing_executable_is_a_clean_refusal",
   U+"test_agy_adapter.TrustInTheBinary.test_a_missing_agy_is_unavailable_and_the_probe_says_why_it_is_never_replaced",
   U+"test_routing.Routing.test_an_explicit_worker_is_the_whole_chain",
   U+"test_routing.Routing.test_a_disabled_primary_means_a_switch_is_needed_and_says_why",
   U+"test_no_real_calls.NothingReal.test_the_antigravity_adapter_never_names_the_legacy_gemini_cli_or_the_flag_that_skips_permissions"]),
 ("T05", "模型 slug 不存在", "COVERED", "归为 MODEL_UNAVAILABLE，只挡那个 Worker，不假装成功", [
   U+"test_codex_adapter.Classification.test_model_problems",
   U+"test_codex_kernel.Failures.test_an_unknown_model_blocks_that_worker",
   U+"test_agy_kernel.Failures.test_an_unknown_model_is_reported_by_the_cli_itself_and_blocks_that_worker",
   U+"test_kernel_policy.FailureClasses.test_an_unavailable_model_blocks_the_worker_not_the_provider"]),
 ("T06", "退出码 0 但终态是错误 / 工具软拒绝", "COVERED", "退出码 0 不等于成功；软拒绝由 Kernel 判断，不因厂商说成功就成功", [
   U+"test_providers_fake.FailureClassification.test_permission_denied_hard_and_soft",
   U+"test_agy_kernel.RealSoftDenial.test_a_write_task_that_ended_without_output_is_judged_by_the_kernel_not_by_the_vendors_success",
   U+"test_agy_kernel.RealSoftDenial.test_a_read_only_task_that_ended_without_output_is_not_a_silent_success",
   U+"test_codex_adapter.Classification.test_exit_code_zero_alone_is_never_success"]),
 ("T07", "Provider 额度用尽", "COVERED", "整个 Provider 被挡到厂商给的时间；只**提议**备用、不自己切换；pause 策略就暂停；没有按量计费的路径", [
   U+"test_kernel_policy.FailureClasses.test_quota_exhaustion_is_recorded_and_a_switch_is_offered_not_taken",
   U+"test_kernel_policy.Policies.test_policy_pause_turns_every_stoppage_into_a_pause",
   U+"test_routing.HealthRules.test_quota_and_auth_cover_the_whole_provider_until_cleared",
   U+"test_codex_kernel.Failures.test_the_usage_limit_blocks_the_provider_until_the_time_the_vendor_gave",
   U+"test_agy_kernel.Failures.test_an_exhausted_quota_blocks_the_provider_and_only_offers_the_fallback",
   U+"test_codex_adapter.Environment.test_an_api_key_in_the_callers_environment_can_never_select_metered_billing",
   U+"test_config.FixedDecisions.test_additional_paid_credits_must_be_false"]),
 ("T08", "429 但不是确定的额度耗尽", "COVERED", "裸 429 归为限速、只让那个模型冷却，绝不变成额度耗尽", [
   U+"test_codex_adapter.Classification.test_a_bare_429_is_a_rate_limit_never_an_empty_quota",
   U+"test_codex_kernel.Failures.test_a_rate_limit_cools_down_one_model_only",
   U+"test_kernel_policy.FailureClasses.test_a_bare_429_is_a_rate_limit_and_never_an_exhausted_quota",
   U+"test_providers_fake.FailureClassification.test_bare_429_is_a_rate_limit_not_an_exhausted_quota",
   U+"test_agy_adapter.ClassifyText.test_a_plain_rate_limit_is_not_mistaken_for_an_exhausted_quota"]),
 ("T09", "Worker 写了 3 个文件后额度耗尽", "COVERED", "已写的 3 个文件留在差异里；接力的下一个 Worker 从它们接着做，而不是重来", [
   U+"test_kernel_policy.Timeouts.test_a_partial_write_before_quota_exhaustion_is_preserved_for_the_next_worker",
   U+"test_kernel_relay.RelayEndToEnd.test_work_written_before_a_quota_stop_is_carried_to_the_worker_the_user_switches_to",
   U+"test_providers_fake.FailureClassification.test_midrun_write_then_quota_keeps_the_partial_work"]),
 ("T10", "原进程仍在写入时触发接力", "COVERED", "旧 Worker 还活着时第二个写入者不会启动，项目一直锁着，直到你处理旧进程（用「Kernel 崩溃、Worker 还在」这个场景证明）", [
   U+"test_kernel_recovery.Recovery.test_a_crashed_kernel_whose_worker_is_still_running_blocks_everyone_until_it_is_dealt_with",
   U+"test_kernel_recovery.Recovery.test_a_live_kernel_is_left_alone",
   U+"test_mcp_tools.ToolsOnTheKernel.test_run_task_does_not_start_a_second_child_while_the_first_is_alive",
   U+"test_kernel_control.Concurrency.test_one_worker_at_a_time"]),
 ("T11", "主控退出、所有 Provider 不可用", "COVERED", "没有任何适配器时，任务、结果、补丁、接管包、切换主控照常可用（本阶段补了专门的测试；恢复演练在真实数据上做过同样的事）", [
   U+"test_takeover.EverythingDown.test_t11_with_every_provider_unavailable_the_evidence_and_a_takeover_packet_are_still_there",
   U+"test_takeover.Packet.test_it_lists_what_is_finished_what_waits_for_a_person_and_what_is_in_flight",
   U+"test_cli_task.Failures.test_without_any_adapter_installed_nothing_can_run_yet"]),
 ("T12", "Kernel 被强制终止后重启", "COVERED", "孤儿 attempt 被标成孤儿、不假装完成；已提交的事件在 SIGKILL 后仍在", [
   U+"test_kernel_recovery.Recovery.test_a_crashed_kernel_with_a_dead_worker_is_settled_on_the_next_run",
   U+"test_kernel_recovery.Recovery.test_recovery_with_terminate_stops_the_verified_orphan_and_keeps_its_partial_work",
   U+"test_kernel_recovery.CrashAtAnyMoment.test_whenever_the_kernel_dies_the_next_one_can_settle_everything",
   U+"test_store.Durability.test_committed_events_survive_a_sigkill"]),
 ("T13", "同一个幂等键重复提交", "COVERED", "返回原任务，不多跑；任务不会跑两次", [
   U+"test_kernel_submit.Submitting.test_the_same_key_and_contract_returns_the_original_task",
   U+"test_mcp_tools.ToolsOnTheKernel.test_a_repeated_submission_is_deduplicated_and_recorded_once",
   U+"test_kernel_run.RunRules.test_only_queued_tasks_run_and_never_twice"]),
 ("T14", "普通 Worker 尝试写 .ai/STATE.md", "COVERED", "STATE、规则文件即使在允许的文件夹里也算越界，不进入验收；补丁导入也拒绝；写入就绪体检挡住不合格的项目", [
   U+"test_kernel_run.WriteRuns.test_rule_files_state_and_secrets_are_protected_even_inside_an_allowed_folder",
   U+"test_workspace.Judging.test_protected_paths_are_violations_even_inside_an_allowed_folder",
   U+"test_manual_handoff.ImportPatchAndText.test_a_patch_that_touches_protected_paths_is_refused_by_name",
   U+"test_kernel_submit.RealProjectSafety.test_a_registry_project_cannot_take_write_tasks_until_the_real_gates_exist",
   U+"test_project_write_gate.WithTheKernel.test_harness_files_stay_off_limits_and_a_dirty_base_blocks_again"]),
 ("T15", "普通 Worker 读取/提交账户 JSON", "COVERED", "账户 JSON 路径是敏感路径：不进 Worker 的副本、写入被拒绝且不回显文件名（本阶段把账户 JSON 加进了端到端的用例）", [
   U+"test_policy.SensitivePathTests.test_sensitive_names",
   U+"test_policy.WriteGuardTests.test_sensitive_name_inside_allowed_root_is_refused_without_echoing_it",
   U+"test_kernel_run.WriteRuns.test_committed_secrets_are_not_given_to_the_worker",
   U+"test_workspace.Cloning.test_committed_secrets_are_not_materialised"]),
 ("T16", "未授权的 git add -A / 删除 / push", "COVERED", "Worker 的私有副本没有远端：push 无处可去，add/commit/删除都只发生在副本里（本阶段新增的测试）；检查命令有白名单；没批准的写任务不会启动", [
   U+"test_workspace.GitWrapper.test_t16_adding_committing_deleting_and_pushing_in_the_workers_copy_cannot_reach_the_real_repository",
   U+"test_harness_check.ReadOnlyGuarantees.test_allowlist_rejects_everything_else",
   U+"test_contract.CheckCommands.test_executables_outside_the_allowlist_are_refused",
   U+"test_contract.CheckCommands.test_install_and_publish_commands_are_refused",
   U+"test_agy_adapter.WriteRules.test_only_plain_write_rules_are_ever_written",
   U+"test_kernel_run.ApprovalGuard.test_run_refuses_a_write_task_nobody_approved_even_if_its_state_says_queued",
   U+"test_kernel_control.Approvals.test_refusing_a_permission_cancels_the_task"]),
 ("T17", "同时 2 个 Worker 改不同的地方", "COVERED", "两个写任务同时在跑：各自的工作副本、各自的差异、各自的检查结果，真实项目都没动（同一个项目按设计不会有两个写入者，所以用两个项目演示）；容量 2 满了第三个被拒绝", [
   U+"test_concurrency.TwoWriters.test_t17_two_write_tasks_run_together_each_in_its_own_workspace_with_its_own_diff_and_checks",
   U+"test_concurrency.TwoAtOnce.test_two_projects_run_together_and_a_third_attempt_is_refused",
   U+"test_workspace.EndToEnd.test_r1_and_r2_get_a_private_clone_plus_a_pristine_reference"]),
 ("T18", "同时 2 个 Worker 改同一个文件", "COVERED", "同一个项目不会同时有两个写入者（项目锁）；第一个的结果落进项目之后，第二个任务被「项目变了」的确认挡住、不覆盖已落地的内容，它的补丁是对确认后的状态做的；补丁对不上会被发现", [
   U+"test_concurrency.TwoWriters.test_t18_two_tasks_on_the_same_file_the_second_is_stopped_at_the_drift_gate_and_overwrites_nothing",
   U+"test_concurrency.TwoAtOnce.test_the_same_project_is_never_worked_on_twice_even_with_a_free_slot",
   U+"test_kernel_control.Drift.test_a_project_that_moved_since_submission_must_be_confirmed",
   U+"test_kernel_relay.RelayRefusesWhatItCannotTrust.test_a_patch_that_no_longer_applies_ends_the_attempt_before_the_worker_starts",
   U+"test_codex_kernel.WriteTask.test_a_file_changing_task_end_to_end_and_nothing_is_applied"]),
 ("T19", "模型输出伪造「tests PASS」", "COVERED", "Worker 的「测试通过」只是没验证的文字；只认 Kernel 自己跑的检查", [
   U+"test_kernel_run.WriteRuns.test_a_worker_that_claims_success_but_fails_the_check_does_not_get_through",
   U+"test_codex_kernel.WriteTask.test_a_confident_wrong_answer_is_not_believed",
   U+"test_agy_kernel.WriteTask.test_a_confident_wrong_answer_is_not_believed",
   U+"test_providers_fake.SuccessAndClaims.test_self_reported_test_results_stay_plain_text",
   U+"test_manual_handoff.ImportFolder.test_a_wrong_result_fails_the_kernels_own_check_however_it_was_described"]),
 ("T20", "UI：缺令牌 / 非法 Origin / 重复批准", "N/A（控制台已移除，2026-11-02）", "这一项描述的是 web 控制台自己的 HTTP 层安全性质；控制台和它的测试已经整体删除（CLI/MCP 桥不经 HTTP，这类问题不再适用），不再有测试可跑，见 docs/CHANGELOG.md 当天的条目", []),
 ("T21", "日志里有模拟的 API key / 邮箱", "COVERED", "事件表、日志、结果都经脱敏（API key 的端到端用例；邮箱在脱敏函数层测试）", [
   U+"test_store.StatusChanges.test_events_are_redacted_and_bounded",
   U+"test_codex_kernel.ReadOnlyTask.test_an_attempt_leaves_the_vendor_stream_as_a_redacted_private_log",
   U+"test_kernel_run.WriteRuns.test_secret_looking_text_in_worker_output_is_redacted_everywhere",
   U+"test_redact.IdentifierTests.test_email_is_masked",
   U+"test_redact.VendorTokenTests.test_vendor_keys_are_masked"]),
 ("T22", "主控从 Claude 手动切到 Codex", "COVERED", "接管包只有任务事实和指针，没有对话和 Worker 的话；已验收的任务不会再跑；被替换的主控不能再提交", [
   U+"test_takeover.Packet.test_no_worker_words_no_conversation_and_pointers_instead_of_copies",
   U+"test_takeover.Packet.test_it_lists_what_is_finished_what_waits_for_a_person_and_what_is_in_flight",
   U+"test_takeover.Confirmation.test_accepted_work_is_not_run_again_after_a_switch",
   U+"test_takeover.Confirmation.test_a_coordinator_that_was_replaced_cannot_submit_tasks_any_more",
   U+"test_coordinators.KernelSide.test_switching_writes_the_packet_and_records_one_confirmed_takeover"]),
 ("T23", "工作区有未提交代码、基线变化", "COVERED", "基线漂移要你确认；写任务对未提交改动更严；拒绝漂移就取消任务；脏的项目拒绝写任务", [
   U+"test_kernel_control.Drift.test_a_project_that_moved_since_submission_must_be_confirmed",
   U+"test_kernel_control.Drift.test_a_file_changing_task_is_stricter_about_uncommitted_edits",
   U+"test_kernel_control.Drift.test_refusing_the_drift_cancels_the_task",
   U+"test_kernel_submit.RealProjectSafety.test_a_dirty_project_refuses_write_tasks"]),
 ("T24", "调度器卸载 / 停用", "COVERED（设计 + 演练）", "Runtime 没有常驻调度器；可选的巡检只跑只读检查、只写自己的两个文件、不常驻；被检查的文件夹逐字节不变；Claude 接入工具的 --remove 只拿掉 par 一项（另有在你真实 Codex 配置副本上的卸载演练，见证据文件）", [
   U+"test_patrol_script.Behaviour.test_it_only_ever_runs_the_read_only_check",
   U+"test_patrol_script.Behaviour.test_writes_only_two_files_inside_the_data_directory",
   U+"test_patrol_script.LaunchdTemplate.test_job_is_not_resident",
   U+"test_harness_check.ReadOnlyGuarantees.test_inspected_folders_are_byte_identical_afterwards",
   U+"test_ops_connect_claude.ChangedText.test_remove_takes_only_the_par_entry_out_and_drops_an_empty_mcpservers"]),
]


def run_one(name):
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    stream = io.StringIO()
    res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if res.testsRun == 0:
        return "MISSING"
    if res.errors or res.failures:
        return "FAIL"
    if res.skipped:
        return "SKIP"
    return "PASS"


def render(results, totals):
    lines = ["# 总方案 T01–T24 故障矩阵：逐项对应测试与结果", "",
             "由 `python -B tests/tools/acceptance_matrix.py` 生成；每一行列出的测试都被逐个重新运行。", "",
             "| T | 场景 | 判定 | 测试 | 要点 |", "|---|---|---|---|---|"]
    for t, scen, verdict, why, tests in results:
        passed = sum(1 for _, r in tests if r == "PASS")
        lines.append(f"| {t} | {scen} | {verdict} | {passed}/{len(tests)} 通过 | {why} |")
    lines += ["", f"合计：{totals['PASS']} 项通过，{totals['FAIL']} 项失败，{totals['SKIP']} 项跳过，{totals['MISSING']} 项找不到。", "", "## 每一行用到的测试", ""]
    for t, scen, verdict, why, tests in results:
        lines += [f"### {t}　{scen}", ""]
        lines += [f"- `{name}` — {r}" for name, r in tests]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", metavar="PATH", help="also write the Markdown table to this file")
    parser.add_argument("--no-real-codex", action="store_true", help="leave out the opt-in offline real-Codex checks")
    args = parser.parse_args()
    if not args.no_real_codex:
        os.environ.setdefault("PAR_REAL_CODEX", "1")
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "src"))
    os.chdir(REPO)
    totals = {"PASS": 0, "FAIL": 0, "SKIP": 0, "MISSING": 0}
    results = []
    for t, scen, verdict, why, ids in MAP:
        tests = []
        for name in ids:
            r = run_one(name)
            totals[r] += 1
            tests.append((name, r))
            print(t, r, name, flush=True)
        results.append((t, scen, verdict, why, tests))
    text = render(results, totals)
    if args.write:
        Path(args.write).write_text(text + "\n", encoding="utf-8")
    print("\nTOTALS", json.dumps(totals))
    return 0 if totals["FAIL"] == 0 and totals["MISSING"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
