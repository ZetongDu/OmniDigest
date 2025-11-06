# test_digest.py
# 用于在本地触发一次日报生成与（如已配置）邮件发送

import argparse
from src.omnidigest.pipeline.run_digest import run_digest_pipeline

def main():
    parser = argparse.ArgumentParser(description="Run OmniDigest once")
    parser.add_argument(
        "--domain",
        default="ai",
        help="Which domain to run (e.g. ai, finance). Default: ai",
    )
    args = parser.parse_args()

    # 跑一次完整流水线：抓取 → 处理 → 摘要/分析 → 报告 → （如配置）发送邮件
    result = run_digest_pipeline(args.domain)

    # 打印输出文件列表（md/html）
    outputs = getattr(result, "output_files", None)
    if outputs:
        print("Generated files:", outputs)
    else:
        print("Pipeline finished (no output_files exposed).")

if __name__ == "__main__":
    main()
