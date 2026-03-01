# dump_bin.py：dump_all 与 dump_update 流程说明

## dump_all

`dump_all` 将**归一化后的 CSV 目录**（每个文件对应一个标的）转换为 qlib 使用的 **bin 格式**，并生成**交易日历**和**标的列表**。入口为：

```bash
python scripts/dump_bin.py dump_all --data_path <normalize_dir> --qlib_dir <qlib_dir> --freq day ...
```

---

## 一、初始化（DumpDataBase.__init__）

1. **路径与参数**
   - `data_path`：归一化 CSV 所在目录（或单个文件），会在此目录下 `glob("*{file_suffix}")` 得到所有待处理文件列表 `df_files`。
   - `qlib_dir`：qlib 数据根目录，下将创建 `calendars/`、`features/`、`instruments/`。
   - `exclude_fields` / `include_fields`：决定哪些列会写入 bin（见下文 `get_dump_fields`）。
   - `date_field_name`、`symbol_field_name`：CSV 中日期、标的列的列名（默认 `date`、`symbol`）。
   - `freq`：频率，如 `day` 或 `1min`，影响日历文件名和 bin 文件名。
   - `file_suffix`：源文件后缀，默认 `.csv`。

2. **派生路径**
   - `_calendars_dir` = `qlib_dir/calendars/`
   - `_features_dir` = `qlib_dir/features/`
   - `_instruments_dir` = `qlib_dir/instruments/`

3. **模式**
   - `DumpDataAll` 使用 `_mode = ALL_MODE`，写 bin 时每个字段会先写入「日期索引 + 整段序列」（见下文 `_data_to_bin`）。

---

## 二、dump_all 的四步流程（DumpDataAll.dump）

`dump()` 依次执行：**收集全量日期与标的区间 → 写日历 → 写标的列表 → 按标的写特征 bin**。

### 步骤 1：_get_all_date() — 收集全量日期与每个标的的起止日期

- **目的**：得到「所有 CSV 里出现过的日期」的并集，以及每个标的的 `[start_datetime, end_datetime]`，供后续日历和 instruments 使用。
- **做法**：
  - 用多进程对 `df_files` 中**每个文件**调用 `_get_date(file_path, as_set=True, is_begin_end=True)`：
    - 读取该 CSV 的 `date_field_name` 列；
    - 返回该文件内日期的 **(min, max)** 以及 **set(日期)**。
  - 把所有文件的日期 set 做**并集** → `all_datetime`。
  - 对每个文件，若 min/max 有效，则生成一行标的信息：`symbol \t start_datetime \t end_datetime`（symbol 由文件名通过 `get_symbol_from_file` 得到，如 `sh600000`），加入 `date_range_list`。
- **结果**：
  - `_kwargs["all_datetime_set"]` = 全市场出现的所有日期集合。
  - `_kwargs["date_range_list"]` = 每行一个标的的 `symbol\tstart\tend` 列表。

### 步骤 2：_dump_calendars() — 写交易日历

- **目的**：生成 qlib 的日历文件，即「全市场所有出现过的日期」排序后的列表。
- **做法**：
  - `_calendars_list` = 将 `all_datetime_set` 转成 `pd.Timestamp` 并**排序**。
  - 调用 `save_calendars(_calendars_list)`，写入 `qlib_dir/calendars/{freq}.txt`，每行一个日期字符串（格式由 `freq` 决定，如 day 为 `%Y-%m-%d`）。

### 步骤 3：_dump_instruments() — 写标的列表（all.txt）

- **目的**：生成 qlib 的 instruments 文件，记录每个标的在日历中的有效起止时间。
- **做法**：
  - 用 `date_range_list` 转成 DataFrame（三列：symbol, start_datetime, end_datetime）。
  - 调用 `save_instruments(...)`，写入 `qlib_dir/instruments/all.txt`，格式为 TSV：`symbol \t start_datetime \t end_datetime`，symbol 会通过 `fname_to_code` 转成统一格式（如大写）。

### 步骤 4：_dump_features() — 按标的写特征 bin

- **目的**：把每个标的的 CSV 按「日历对齐」后，按**列（字段）**写入二进制文件，供 qlib 按 symbol + 字段读取。
- **做法**：
  - 对 `df_files` 中**每个文件**多进程调用 `_dump_bin(file_path, calendar_list=_calendars_list)`：
    1. **解析标的**：`code = get_symbol_from_file(file_path)`（如 `sh600000`）。
    2. **读 CSV**：`_get_source_data(file_path)`，并把 `date_field_name` 转成 datetime。
    3. **去重**：按 `date_field_name` 去重，避免 reindex 报错。
    4. **创建该标的目录**：`features_dir = qlib_dir/features/{code}/`（code 会转成小写文件名形式）。
    5. **调用 _data_to_bin(df, calendar_list, features_dir)**，见下。

---

## 三、单标的写入 bin 的细节：_data_to_bin

- **输入**：该标的的 DataFrame `df`、全局 `calendar_list`、该标的的 `features_dir`。
- **步骤**：

1. **按日历对齐**
   - `data_merge_calendar(df, calendar_list)`：
     - 取日历中落在 `[df.date.min(), df.date.max()]` 的区间；
     - 以该区间日历为 index 对 `df` 做 `reindex`，缺失日期填 NaN。
   - 得到与日历对齐的 `_df`，index 为日期。

2. **日期在全局日历中的起始下标**
   - `date_index = get_datetime_index(_df, calendar_list)` = `calendar_list.index(_df.index.min())`，即该标的**第一个日期**在**全市场日历**中的下标（从 0 开始）。该值会写入每个 bin 的**第一个 float**，供 qlib 解析时知道这段数据从日历的哪一天开始。

3. **按列写 bin（ALL_MODE）**
   - `get_dump_fields(_df.columns)`：若指定了 `include_fields` 则只写这些列；否则为「所有列 - exclude_fields」；都未指定则写全部列。
   - 对每个要 dump 的字段 `field`：
     - 文件路径：`features_dir/{field.lower()}.{freq}.bin`（例如 `open.day.bin`）。
     - 在 **dump_all** 下 `_mode == ALL_MODE`，且通常是新建文件，走 **else** 分支：
       - 写入内容：`np.hstack([date_index, _df[field]]).astype("<f").tofile(bin_path)`。
       - 即：**第一个数为 date_index（float 形式），后面是该标的在该字段上按日历对齐的一维序列**（缺失为 NaN，以 float 存储）。
   - 数据类型：小端 float32（`<f`）。

---

## 四、输出目录结构小结

执行完成后，`qlib_dir` 下大致为：

```
qlib_dir/
├── calendars/
│   └── day.txt              # 全市场交易日历，每行一个日期
├── instruments/
│   └── all.txt              # 标的列表，每行: symbol \t start_datetime \t end_datetime
└── features/
    ├── sh600000/
    │   ├── open.day.bin     # 第一个 float=date_index，其后为 open 序列
    │   ├── high.day.bin
    │   ├── low.day.bin
    │   ├── close.day.bin
    │   ├── volume.day.bin
    │   ├── factor.day.bin
    │   └── change.day.bin
    ├── sz000001/
    │   └── ...
    └── ...
```

- 每个 bin 文件：**一个 float32 的 date_index + 与日历对齐的该字段序列**，qlib 读取时用 date_index 知道该标的从日历的第几天开始，再按长度切出该标的的区间。

---

## 五、与 dump_update 的差异（简要）

- **dump_all**：不依赖已有 qlib 目录，从所有 CSV 重新收集日历与标的，并**全量**写每个标的的 bin（每个字段写入 `date_index + 整段序列`）。
- **dump_update**：依赖已有 `calendars/day.txt` 和 `instruments/all.txt`，只对「有新日期」的标的做**追加**：在已有 bin 末尾 `ab` 追加新日期的序列（**不再写 date_index**），并更新 instruments 的 end_datetime。

上述即为 `dump_all` 的完整流程。

---

# dump_update 的详细流程

`dump_update` 在**已有 qlib 数据**基础上，将**新归一化 CSV** 中的**新日期**追加到对应 bin 并更新日历和标的列表。入口为：

```bash
python scripts/dump_bin.py dump_update --data_path <normalize_dir> --qlib_dir <qlib_dir> --freq day ...
```

**前置条件**：`qlib_dir` 下已存在 `calendars/{freq}.txt` 和 `instruments/all.txt`（通常由一次 `dump_all` 或 GetData 生成）。

---

## 一、初始化（DumpDataUpdate.__init__）

在基类初始化（路径、字段过滤等）之后，额外完成：

1. **模式**  
   - `_mode = UPDATE_MODE`。在 `_data_to_bin` 里若 bin 已存在则走**追加**分支（只写新数据，不写 date_index）。

2. **读入已有 qlib 元数据**  
   - `_old_calendar_list`：从 `qlib_dir/calendars/{freq}.txt` 读入的**当前日历**（已排序的日期列表）。  
   - `_update_instruments`：从 `qlib_dir/instruments/all.txt` 读入的标的表，转成 `dict`，key 为 symbol，value 为 `{start_datetime, end_datetime}`。用于判断标的是「已存在」还是「新标的」，以及已存在标的的当前 end 日期。

3. **加载本次所有 CSV**  
   - `_load_all_source_data()`：多线程读取 `data_path` 下所有 `*{file_suffix}` 文件，合并成一个大 DataFrame `_all_data`（含 `date_field_name`、`symbol_field_name` 及各特征列）。  
   - 若 CSV 没有 symbol 列，则用文件名通过 `get_symbol_from_file` 补上。  
   - **注意**：所有数据常驻内存，数据量很大时需保证内存足够。

4. **计算新日历**  
   - `_new_calendar_list` = **旧日历** + **本次 CSV 中大于旧日历最后一天的所有日期**（去重排序）。  
   - 即：在旧日历末尾追加「本次新增的交易日」，得到更新后的全量日历。

---

## 二、dump_update 的三步流程（DumpDataUpdate.dump）

`dump()` 依次执行：**写新日历 → 按标的追加/新建 bin → 写更新后的 instruments**。

### 步骤 1：save_calendars(_new_calendar_list)

- **目的**：用「旧日历 + 本次新日期」覆盖 `qlib_dir/calendars/{freq}.txt`。  
- **做法**：直接调用基类 `save_calendars`，将 `_new_calendar_list` 格式化为字符串并写回日历文件。  
- **结果**：后续 qlib 读取日历时会看到新的交易日。

### 步骤 2：_dump_features() — 按标的写入或追加 bin

- **目的**：对每个在 `_all_data` 里出现的标的，若有**新日期**则只把新日期的数据追加到已有 bin；若是**新标的**则像 dump_all 一样写一整段（date_index + 全序列）。
- **做法**：  
  - 按 `symbol_field_name` 对 `_all_data` 做 `groupby`，得到每个标的的 DataFrame `_df`。  
  - 对每个标的解析出统一 code（如 `SH600000`），并取该标的的 `_start, _end`（`_get_date(_df, is_begin_end=True)`）。  
  - **若该 code 已在 `_update_instruments` 中（已存在标的）**：  
    - 取该标的在 instruments 里记录的 `end_datetime`。  
    - `_update_calendars` = `_df` 中**严格大于**该 `end_datetime` 的日期，排序后的列表（即「本次新增的交易日」）。  
    - 若 `_update_calendars` 非空：  
      - 把该标的在 instruments 中的 `end_datetime` 更新为当前 `_end`（即该标的在本次数据中的最大日期）。  
      - 提交任务：`_dump_bin(_df, _update_calendars)`。  
    - 若 `_update_calendars` 为空：该标的没有新日期，不写 bin。  
  - **若该 code 不在 `_update_instruments` 中（新标的）**：  
    - 在 `_update_instruments` 中新增一项：`start_datetime`、`end_datetime` 设为该标的的 `_start`、`_end`。  
    - 提交任务：`_dump_bin(_df, self._new_calendar_list)`（传入**全量新日历**，和 dump_all 行为一致）。  
  - 所有任务用进程池执行，收集异常到 `error_code`。

**与 _dump_bin / _data_to_bin 的配合**：

- **已存在标的**：传入的 `calendar_list` = `_update_calendars`（仅新日期）。  
  - `data_merge_calendar(df, _update_calendars)` 得到的是**只含新日期**、与该标的数据对齐的 `_df`（缺失日为 NaN）。  
  - 对每个字段，若 `bin_path` 已存在且 `_mode == UPDATE_MODE`，则**不写 date_index**，只把 `_df[field]` 的序列以 `ab` 方式追加到对应 bin 末尾。  
  - 因此：**只追加“新日期”对应的那一段 float32 序列**。  

- **新标的**：传入的 `calendar_list` = `_new_calendar_list`（全量日历）。  
  - 与 dump_all 相同：对齐到全量日历，写 `date_index + 整段序列`，新建该标的目录下的各 `*.bin`。

### 步骤 3：save_instruments(更新后的 _update_instruments)

- **目的**：把本次更新后的标的表（含已存在标的的新 end、以及新标的的 start/end）写回 `qlib_dir/instruments/all.txt`。  
- **做法**：将 `_update_instruments` 转成 DataFrame（列：symbol, start_datetime, end_datetime），调用基类 `save_instruments`，覆盖 all.txt。  
- **结果**：qlib 读取标的列表时会看到每个标的的最新时间范围。

---

## 三、dump_update 下 _data_to_bin 的分支

- **bin 不存在**（新标的）：  
  - 与 dump_all 相同：`np.hstack([date_index, _df[field]]).astype("<f").tofile(bin_path)`。  

- **bin 已存在且 _mode == UPDATE_MODE**（已存在标的，只追加新日期）：  
  - 用 `bin_path.open("ab")` 追加写入：`np.array(_df[field]).astype("<f").tofile(fp)`。  
  - 不写 date_index，只写本次传入的 `_df` 中该字段的一维序列（长度 = 本次 calendar_list 长度，即新日期个数）。

---

## 四、dump_update 流程小结

| 阶段       | 内容 |
|------------|------|
| 初始化     | 读旧日历、旧 instruments；加载 data_path 下全部 CSV 为 _all_data；计算 _new_calendar_list = 旧日历 + 本次新日期。 |
| 第一步     | 用 _new_calendar_list 覆盖 calendars/{freq}.txt。 |
| 第二步     | 按标的：已存在且有新日期 → 只把新日期的各字段序列追加到对应 bin；新标的 → 按全量新日历写 date_index + 全序列；同时维护 _update_instruments（end 或新增 start/end）。 |
| 第三步     | 把 _update_instruments 写回 instruments/all.txt。 |

**与 dump_all 的对比**：

- dump_all：不读已有 qlib；日历和 instruments 完全由本次 CSV 生成；每个标的的每个字段都是「date_index + 整段序列」新建写入。  
- dump_update：强依赖已有日历和 instruments；日历在旧基础上追加新日期；已存在标的只追加新日期对应的片段（无 date_index），新标的才写整段；最后统一更新 instruments。
