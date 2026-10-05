# gitsize

找出是什么让你的 git 仓库变胖的小工具：在仓库里运行，一眼看到历史上最大的文件、它们是谁引入的、以及哪些大文件已经删了却还在占历史体积。

纯标准库（`subprocess` / `argparse` / `sys` / `json` / `collections`），零依赖。**只读分析，不做任何写入或破坏性操作。**

## 安装

```bash
git clone https://github.com/ljiang9/gitsize.git
cd gitsize
python3 -m gitsize --help
```

## 用法

```bash
cd 你的仓库
python3 -m gitsize              # 体积总览 + 历史上最大的 20 个文件
python3 -m gitsize --top 10    # 只看前 10
python3 -m gitsize --ext       # 按扩展名分组统计
python3 -m gitsize --json      # 机器可读输出
python3 -m gitsize --suggest   # 清理建议（只建议，不执行）
```

示例输出：

```
===== 仓库体积 =====
对象总数：128 个 blob（pack 内 120，零散 8）
磁盘占用：约 45.2 MB（pack 44.9 MB + 零散 312.0 KB）
历史 blob 累计：61.7 MB
HEAD 中最大文件：assets/demo.mp4（8.4 MB）

===== 历史上最大的 20 个文件 =====
        大小  状态  路径 / 引入提交
  ------------------------------------------------------------
     8.4 MB  现存  assets/demo.mp4
                   └─ 首次引入：a1b2c3d 添加演示视频
     5.1 MB  已删  data/raw-dump.csv
                   └─ 首次引入：e4f5a6b 导入原始数据
```

## 参数

| 参数 | 说明 |
|---|---|
| `--top N` | 显示历史上最大的 N 个文件（默认 20） |
| `--ext` | 按扩展名分组统计历史累计体积 |
| `--json` | 输出 JSON（字段含义见下） |
| `--suggest` | 给出清理建议（纯文字建议，不做任何操作） |
| `--version` | 显示版本号 |

退出码：`0` 成功；`1` 不在 git 仓库 / 仓库无提交；`2` 参数错误。

## JSON 字段

- `blob_count` / `total_history_bytes`：历史 blob 总数与累计字节
- `pack_kb` / `loose_kb` / `in_pack` / `loose_count`：来自 `git count-objects -v`
- `head_biggest`：HEAD 中最大的文件及大小
- `top[]`：`sha` / `path` / `size` / `in_head`（是否仍在 HEAD）/ `introduced_by` / `introduced_subject`
- `reclaimable_bytes`：top 中"已删但仍在历史"的文件体积之和
- `ext_groups[]`：按扩展名分组的体积与文件数

## 工作原理

1. `git rev-list --objects --all` 枚举历史上所有对象；
2. `git cat-file --batch-check` 一次查出全部 blob 的大小；
3. `git log --find-object=<sha>` 找到首次引入该 blob 的提交；
4. `git ls-tree HEAD` 判断文件是否仍在当前分支；
5. `git count-objects -v` 读 pack/零散对象体积。

## 诚实说明（已知局限）

- **"引入提交"是近似值**：用 `--find-object` 找触及该 blob 的最早提交；文件被改名/移动后，路径层面的追踪可能不准，sha 层面是准的。
- **体积是逻辑大小**：blob 的未压缩字节数之和；git pack 的 delta 压缩会让实际磁盘占用更小，以 `git count-objects` 的磁盘数字为准。
- `--suggest` 只是文字建议：**本工具永远不执行 `filter-repo` / BFG / `gc` 等破坏性操作**，重写历史会改变所有提交 hash，操作前请备份并与协作者协调。
- 大仓库（数万对象）首次扫描可能需要几秒到几十秒；这是一次性成本。
- 需要本地有 `git` 命令；不支持 bare 仓库之外的特殊情况（如 worktree 里运行是支持的）。
