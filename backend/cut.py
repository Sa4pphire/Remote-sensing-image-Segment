import argparse
import json
import math
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr
from PIL import Image
from tqdm import tqdm


gdal.UseExceptions()
ogr.UseExceptions()

WINDOW_SIZE = 512
STRIDE = 256
JPEG_QUALITY = 95
LABEL_CLASS_VALUE = 255


def open_raster(path):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"无法打开影像：{path}")
    return dataset


def open_vector(path):
    dataset = ogr.Open(str(path), 0)
    if dataset is None:
        raise RuntimeError(f"无法打开标注：{path}")
    return dataset


def tile_starts(length, window_size, stride):
    """返回覆盖完整影像所需的切片起点。"""
    if length <= window_size:
        return [0]
    count = math.ceil((length - window_size) / stride) + 1
    return [index * stride for index in range(count)]


def read_padded_tile(dataset, x_offset, y_offset, window_size):
    """读取影像窗口，并用 0 填充超出影像边界的区域。"""
    read_width = min(window_size, dataset.RasterXSize - x_offset)
    read_height = min(window_size, dataset.RasterYSize - y_offset)
    data = dataset.ReadAsArray(
        x_offset,
        y_offset,
        read_width,
        read_height,
    )
    if data is None:
        raise RuntimeError(f"读取影像失败：x={x_offset}, y={y_offset}")
    if data.ndim == 2:
        data = data[np.newaxis, :, :]
    if read_width == window_size and read_height == window_size:
        return data

    padded = np.zeros(
        (data.shape[0], window_size, window_size),
        dtype=data.dtype,
    )
    padded[:, :read_height, :read_width] = data
    return padded


def tile_geotransform(source_geotransform, x_offset, y_offset):
    origin_x = (
        source_geotransform[0]
        + x_offset * source_geotransform[1]
        + y_offset * source_geotransform[2]
    )
    origin_y = (
        source_geotransform[3]
        + x_offset * source_geotransform[4]
        + y_offset * source_geotransform[5]
    )
    return (
        origin_x,
        source_geotransform[1],
        source_geotransform[2],
        origin_y,
        source_geotransform[4],
        source_geotransform[5],
    )


def tile_bounds(geotransform, width, height):
    corners = [
        gdal.ApplyGeoTransform(geotransform, 0, 0),
        gdal.ApplyGeoTransform(geotransform, width, 0),
        gdal.ApplyGeoTransform(geotransform, 0, height),
        gdal.ApplyGeoTransform(geotransform, width, height),
    ]
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return min(xs), min(ys), max(xs), max(ys)


def rasterize_label_tile(label_layer, geotransform, projection, window_size):
    """按当前影像网格栅格化面标注；没有标签时返回 None。"""
    x_min, y_min, x_max, y_max = tile_bounds(
        geotransform,
        window_size,
        window_size,
    )
    label_layer.SetSpatialFilterRect(x_min, y_min, x_max, y_max)

    try:
        if label_layer.GetFeatureCount() == 0:
            return None

        mask_dataset = gdal.GetDriverByName("MEM").Create(
            "",
            window_size,
            window_size,
            1,
            gdal.GDT_Byte,
        )
        mask_dataset.SetGeoTransform(geotransform)
        mask_dataset.SetProjection(projection)
        mask_band = mask_dataset.GetRasterBand(1)
        mask_band.Fill(0)
        mask_band.SetNoDataValue(0)

        result = gdal.RasterizeLayer(
            mask_dataset,
            [1],
            label_layer,
            burn_values=[LABEL_CLASS_VALUE],
        )
        if result != 0:
            raise RuntimeError("面标注栅格化失败")

        label = mask_band.ReadAsArray()
        mask_dataset = None
        if label is None or not np.any(label):
            return None
        return label
    finally:
        label_layer.SetSpatialFilter(None)


def save_image_jpeg(data, output_path):
    """保存前三个波段为高质量 RGB JPEG。"""
    if data.shape[0] < 3:
        raise ValueError("输入影像少于三个波段")
    if data.dtype != np.uint8:
        raise ValueError(f"JPEG 影像必须是 8 位无符号数据，当前为 {data.dtype}")

    rgb = np.moveaxis(data[:3], 0, -1)
    Image.fromarray(rgb, mode="RGB").save(
        output_path,
        format="JPEG",
        quality=JPEG_QUALITY,
        subsampling=0,
    )


def save_label_png(label, output_path):
    """无损保存标签，避免 JPEG 改变类别像元值。"""
    Image.fromarray(label, mode="L").save(
        output_path,
        format="PNG",
        compress_level=6,
    )


def collect_outputs(image_output_dir, label_output_dir):
    images = {
        path.stem: path for path in image_output_dir.glob("*.jpg")
    }
    labels = {
        path.stem: path for path in label_output_dir.glob("*.png")
    }
    return images, labels


def remove_temporary_outputs(image_output_dir, label_output_dir):
    """清理被中断任务留下的临时文件，不影响已经完成的样本对。"""
    temporary_stems = set()
    for path in image_output_dir.glob(".crop_*.jpg.tmp"):
        temporary_stems.add(path.name[1 : -len(".jpg.tmp")])
    for path in label_output_dir.glob(".crop_*.png.tmp"):
        temporary_stems.add(path.name[1 : -len(".png.tmp")])

    for stem in temporary_stems:
        (image_output_dir / f"{stem}.jpg").unlink(missing_ok=True)
        (label_output_dir / f"{stem}.png").unlink(missing_ok=True)
        (image_output_dir / f".{stem}.jpg.tmp").unlink(missing_ok=True)
        (label_output_dir / f".{stem}.png.tmp").unlink(missing_ok=True)


def ensure_one_to_one(image_output_dir, label_output_dir):
    images, labels = collect_outputs(image_output_dir, label_output_dir)
    missing_labels = set(images) - set(labels)
    missing_images = set(labels) - set(images)
    if missing_labels or missing_images:
        raise RuntimeError(
            "image 和 lable 存在未配对文件："
            f"缺标签 {len(missing_labels)}，缺影像 {len(missing_images)}"
        )
    return images, labels


def remove_empty_pairs(image_output_dir, label_output_dir):
    """删除全零标签以及同编号影像。"""
    images, labels = ensure_one_to_one(
        image_output_dir,
        label_output_dir,
    )
    empty_stems = []
    for stem, label_path in labels.items():
        with Image.open(label_path) as label:
            label.load()
            if label.getbbox() is None:
                empty_stems.append(stem)

    for stem in empty_stems:
        labels[stem].unlink()
        images[stem].unlink()
    return len(empty_stems)


def validate_inputs(image_dataset, label_layer):
    if image_dataset.RasterCount < 3:
        raise ValueError("输入影像少于三个波段，无法导出 RGB JPEG")
    if image_dataset.GetRasterBand(1).DataType != gdal.GDT_Byte:
        raise ValueError("输入影像不是 8 位无符号数据")

    image_srs = image_dataset.GetSpatialRef()
    label_srs = label_layer.GetSpatialRef()
    if image_srs is None or label_srs is None:
        raise ValueError("影像或 sf.shp 缺少坐标系")
    if not image_srs.IsSame(label_srs):
        raise ValueError("1.tif 与 sf.shp 的坐标系不一致")

    geometry_type = ogr.GT_Flatten(label_layer.GetLayerDefn().GetGeomType())
    if geometry_type not in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
        raise ValueError("sf.shp 必须是面要素")


def validate_completed_outputs(
    image_output_dir,
    label_output_dir,
    window_size,
):
    """完整验证配对、格式、尺寸、类别值和非空标签。"""
    images, labels = ensure_one_to_one(
        image_output_dir,
        label_output_dir,
    )
    invalid_images = []
    invalid_labels = []

    for stem in sorted(images):
        try:
            with Image.open(images[stem]) as image:
                image.load()
                if (
                    image.format != "JPEG"
                    or image.mode != "RGB"
                    or image.size != (window_size, window_size)
                ):
                    invalid_images.append(stem)
        except Exception:
            invalid_images.append(stem)

        try:
            with Image.open(labels[stem]) as label:
                label.load()
                values = set(np.unique(np.asarray(label)).tolist())
                if (
                    label.format != "PNG"
                    or label.mode != "L"
                    or label.size != (window_size, window_size)
                    or not values.issubset({0, LABEL_CLASS_VALUE})
                    or LABEL_CLASS_VALUE not in values
                ):
                    invalid_labels.append(stem)
        except Exception:
            invalid_labels.append(stem)

    if invalid_images or invalid_labels:
        raise RuntimeError(
            "输出质量校验失败："
            f"异常影像 {len(invalid_images)}，异常标签 {len(invalid_labels)}"
        )
    return len(images)


def export_dataset(
    image_path,
    label_vector_path,
    image_output_dir,
    label_output_dir,
    window_size=WINDOW_SIZE,
    stride=STRIDE,
    progress_callback=None,
):
    image_output_dir.mkdir(parents=True, exist_ok=True)
    label_output_dir.mkdir(parents=True, exist_ok=True)
    remove_temporary_outputs(image_output_dir, label_output_dir)
    ensure_one_to_one(image_output_dir, label_output_dir)

    image_dataset = open_raster(image_path)
    label_dataset = open_vector(label_vector_path)
    label_layer = label_dataset.GetLayer(0)
    validate_inputs(image_dataset, label_layer)

    x_starts = tile_starts(image_dataset.RasterXSize, window_size, stride)
    y_starts = tile_starts(image_dataset.RasterYSize, window_size, stride)
    total_windows = len(x_starts) * len(y_starts)

    print(f"输入影像：{image_path}")
    print(f"输入标注：{label_vector_path}")
    print(f"面要素数量：{label_layer.GetFeatureCount()}")
    print(
        f"影像尺寸：{image_dataset.RasterXSize}×{image_dataset.RasterYSize}；"
        f"共检查 {total_windows} 个窗口。"
    )

    def report(event, **payload):
        if progress_callback is not None:
            progress_callback(event, **payload)

    report(
        "started",
        total=total_windows,
        width=image_dataset.RasterXSize,
        height=image_dataset.RasterYSize,
        feature_count=label_layer.GetFeatureCount(),
    )

    projection = image_dataset.GetProjection()
    source_geotransform = image_dataset.GetGeoTransform()
    exported_pairs = 0
    skipped_empty = 0
    removed_stale_empty = 0

    progress = tqdm(
        total=total_windows,
        desc="建筑物切片进度",
        unit="窗",
        mininterval=2,
        disable=progress_callback is not None,
    )

    try:
        tile_index = 0
        for y_offset in y_starts:
            for x_offset in x_starts:
                stem = f"crop_{tile_index:06d}"
                image_output_path = image_output_dir / f"{stem}.jpg"
                label_output_path = label_output_dir / f"{stem}.png"

                if image_output_path.exists() != label_output_path.exists():
                    raise RuntimeError(f"发现未配对输出：{stem}")

                geotransform = tile_geotransform(
                    source_geotransform,
                    x_offset,
                    y_offset,
                )
                label = rasterize_label_tile(
                    label_layer,
                    geotransform,
                    projection,
                    window_size,
                )

                if label is None:
                    if image_output_path.exists():
                        label_output_path.unlink()
                        image_output_path.unlink()
                        removed_stale_empty += 1
                    skipped_empty += 1
                    tile_index += 1
                    progress.update(1)
                    report(
                        "progress",
                        current=tile_index,
                        total=total_windows,
                        exported=exported_pairs,
                        skipped=skipped_empty,
                    )
                    continue

                if not image_output_path.exists():
                    image_tile = read_padded_tile(
                        image_dataset,
                        x_offset,
                        y_offset,
                        window_size,
                    )
                    temporary_image_path = image_output_dir / f".{stem}.jpg.tmp"
                    temporary_label_path = label_output_dir / f".{stem}.png.tmp"
                    save_image_jpeg(image_tile, temporary_image_path)
                    save_label_png(label, temporary_label_path)
                    temporary_image_path.replace(image_output_path)
                    temporary_label_path.replace(label_output_path)

                exported_pairs += 1
                tile_index += 1
                progress.update(1)
                report(
                    "progress",
                    current=tile_index,
                    total=total_windows,
                    exported=exported_pairs,
                    skipped=skipped_empty,
                )
    finally:
        progress.close()
        label_layer.SetSpatialFilter(None)
        label_dataset = None
        image_dataset = None

    removed_empty = remove_empty_pairs(
        image_output_dir,
        label_output_dir,
    )
    verified_pairs = validate_completed_outputs(
        image_output_dir,
        label_output_dir,
        window_size,
    )

    print(f"有效建筑物样本：{verified_pairs} 对")
    print(f"跳过空标签窗口：{skipped_empty} 个")
    print(f"删除旧空标签对：{removed_stale_empty + removed_empty} 对")
    print(f"影像输出：{image_output_dir}")
    print(f"标签输出：{label_output_dir}")
    report(
        "completed",
        total=total_windows,
        exported=verified_pairs,
        skipped=skipped_empty,
        removed=removed_stale_empty + removed_empty,
        image_output=str(image_output_dir),
        label_output=str(label_output_dir),
    )


def _emit_json_progress(event, **payload):
    """向前端输出一行稳定的机器可读进度事件。"""
    message = {"event": event, **payload}
    print(f"CUTPY_PROGRESS {json.dumps(message, ensure_ascii=False)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="遥感影像建筑物样本切片工具")
    parser.add_argument("--image", type=Path, help="输入遥感影像（GeoTIFF 等栅格文件）")
    parser.add_argument("--label", type=Path, help="输入面标注 Shapefile")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出根目录；程序会创建 image 和 lable 子目录",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=WINDOW_SIZE,
        help=f"切片尺寸，默认 {WINDOW_SIZE}",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=STRIDE,
        help=f"滑窗步长，默认 {STRIDE}",
    )
    parser.add_argument(
        "--progress-json",
        action="store_true",
        help="额外输出供前端解析的进度事件",
    )
    args = parser.parse_args()

    if args.image is None or args.label is None or args.output_dir is None:
        parser.error("必须同时提供 --image、--label 和 --output-dir")
    if args.window_size <= 0 or args.stride <= 0:
        parser.error("--window-size 和 --stride 必须为正整数")

    image_output_dir = args.output_dir / "image"
    label_output_dir = args.output_dir / "lable"
    callback = _emit_json_progress if args.progress_json else None

    try:
        export_dataset(
            image_path=args.image,
            label_vector_path=args.label,
            image_output_dir=image_output_dir,
            label_output_dir=label_output_dir,
            window_size=args.window_size,
            stride=args.stride,
            progress_callback=callback,
        )
    except Exception as error:
        if args.progress_json:
            _emit_json_progress("error", message=str(error))
        raise


if __name__ == "__main__":
    main()
