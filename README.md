# RSIS 

 RSIS是一个面向 ArcGIS 遥感影像的训练数据切片工具。它将遥感栅格影像与面要素标注配对处理，生成可直接用于目标检测或语义分割数据集制作的影像切片和标签切片。

## 功能

- 支持 GeoTIFF 等 GDAL 可读取的栅格影像。
- 支持 Polygon 和 MultiPolygon Shapefile 标注。
- 默认切片大小为 `512 × 512`，默认步长为 `256`。
- 影像输出为 RGB JPEG，标签输出为无损灰度 PNG。
- 自动跳过没有标注的窗口。
- 自动校验影像与标签是否一一配对、尺寸是否正确、格式是否正确、标签是否包含前景值 `255`。
- 支持任务中断后重新运行，已完成的样本对会被复用，残留临时文件会被清理。
- 前端显示窗口处理进度、有效样本数量、空标签数量和后端日志。

### 安装依赖

普通 Python 环境可以先运行：

```powershell
python -m pip install -r requirements.txt
```

GDAL 在 Windows 上可能需要使用 Conda 或与你的 Python 版本匹配的预编译包。使用 Conda 时推荐：

```powershell
conda create -n cutpy python=3.13 -y
conda activate cutpy
conda install -c conda-forge gdal numpy pillow tqdm -y
```

安装完成后，可以检查关键依赖：

```powershell
python -c "from osgeo import gdal, ogr; import numpy, PIL, tqdm; print(gdal.VersionInfo())"
```

## 启动前端

在项目根目录双击：

```text
启动 Cutpy.bat
```

或者在 PowerShell 中运行：

```powershell
python .\app.py
```

界面中依次选择：

1. 遥感影像：选择 `.tif`、`.tiff`、`.img` 或其他 GDAL 支持的栅格文件。
2. 面标注：选择 `.shp` 文件。Shapefile 的 `.dbf`、`.shx`、`.prj` 等配套文件应放在同一目录。
3. 输出文件夹：选择数据集输出根目录。

点击“开始切片”后，程序会自动创建：

```text
输出文件夹/
├─ image/       # RGB JPEG 影像切片
└─ lable/       # 灰度 PNG 标签切片
```

这里保留了后端已有的 `lable` 目录命名，以保证与现有数据处理流程兼容。

## 命令行运行后端

如果不需要图形界面，也可以直接运行后端：

```powershell
python .\backend\cut.py `
  --image "D:\data\image.tif" `
  --label "D:\data\building.shp" `
  --output-dir "D:\data\dataset" `
  --progress-json
```

可选参数：

```text
--window-size  切片尺寸，默认 512
--stride       滑动步长，默认 256
--progress-json 额外输出供前端解析的进度事件
```

## 输入数据注意事项

- 影像和标注的空间范围应有实际重叠，否则可能得到 0 个有效样本。
- 影像和标注的坐标系必须一致；程序会在开始切片前检查。
- 影像第一波段必须是 8 位无符号数据，且影像至少有 3 个波段。
- 标签使用 PNG 保存，不要改成 JPEG；有损压缩可能改变类别像元值。
- 如果输出目录中存在未配对的旧文件，程序会停止并提示，避免混合不同数据集。

