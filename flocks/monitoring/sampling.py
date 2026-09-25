"""Historical sample labels for old reports; development execution is retired."""
DESCRIPTION = '历史开发联调：不限处置状态，优先中危/高危/严重，每轮随机分析 1 条；仅统计样本。'


def enabled(policy):
    return False
