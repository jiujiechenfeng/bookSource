# bookSource

收集「阅读」APP 书源，生成 `shuyuan.json`，并校验可访问的书源输出到 `xiang.json`。

## 参考项目

- 书源集合参考：[aoaostar/legado](https://github.com/aoaostar/legado)
- 校验思路参考：[CalmXin/xin-verify-book-source](https://github.com/CalmXin/xin-verify-book-source)

## 使用

拉取默认全量书源并合并本地 `shuyuan.json`：

```bash
python3 scripts/booksource.py collect
```

校验本地书源，生成 `xiang.json`：

```bash
python3 scripts/booksource.py verify
```

校验默认支持断点继续：程序会读取已有的 `xiang.json` 和 `error.json`，跳过已经校验过的书源，并在运行中每 25 条自动保存一次。

一步完成拉取和校验：

```bash
python3 scripts/booksource.py all
```

常用参数：

- `-u/--url`：追加书源 JSON 直链，可传多次。
- `-w/--workers`：校验并发数，默认 `32`。
- `--timeout`：单个书源访问超时时间，默认 `5` 秒。
- `--save-every`：每校验多少条保存一次断点，默认 `25`。
- `--no-resume`：不使用断点，重新校验全部书源。
- `--verify-ssl`：启用严格 SSL 校验；默认关闭，以兼容更多旧书源。

校验逻辑只判断 `bookSourceUrl` 是否能访问，不保证搜索、目录、正文规则一定可用。
