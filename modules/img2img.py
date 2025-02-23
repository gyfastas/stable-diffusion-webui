import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, ImageFilter, ImageEnhance, ImageChops, UnidentifiedImageError

from modules import sd_samplers
from modules.generation_parameters_copypaste import create_override_settings_dict
from modules.processing import Processed, StableDiffusionProcessingImg2Img, process_images
from modules.shared import opts, state
import modules.shared as shared
import modules.processing as processing
from modules.ui import plaintext_to_html
import modules.scripts


def process_batch(p, input_dir, output_dir, inpaint_mask_dir, args, to_scale=False, scale_by=1.0):
    processing.fix_seed(p)

    images = shared.listfiles(input_dir)

    is_inpaint_batch = False
    if inpaint_mask_dir:
        inpaint_masks = shared.listfiles(inpaint_mask_dir)
        is_inpaint_batch = bool(inpaint_masks)

        if is_inpaint_batch:
            print(f"\nInpaint batch is enabled. {len(inpaint_masks)} masks found.")

    print(f"Will process {len(images)} images, creating {p.n_iter * p.batch_size} new images for each.")

    save_normally = output_dir == ''

    p.do_not_save_grid = True
    p.do_not_save_samples = not save_normally

    state.job_count = len(images) * p.n_iter

    for i, image in enumerate(images):
        state.job = f"{i+1} out of {len(images)}"
        if state.skipped:
            state.skipped = False

        if state.interrupted:
            break

        try:
            img = Image.open(image)
        except UnidentifiedImageError as e:
            print(e)
            continue
        # Use the EXIF orientation of photos taken by smartphones.
        img = ImageOps.exif_transpose(img)

        if to_scale:
            p.width = int(img.width * scale_by)
            p.height = int(img.height * scale_by)

        p.init_images = [img] * p.batch_size

        image_path = Path(image)
        if is_inpaint_batch:
            # try to find corresponding mask for an image using simple filename matching
            if len(inpaint_masks) == 1:
                mask_image_path = inpaint_masks[0]
            else:
                # try to find corresponding mask for an image using simple filename matching
                mask_image_dir = Path(inpaint_mask_dir)
                masks_found = list(mask_image_dir.glob(f"{image_path.stem}.*"))

                if len(masks_found) == 0:
                    print(f"Warning: mask is not found for {image_path} in {mask_image_dir}. Skipping it.")
                    continue

                # it should contain only 1 matching mask
                # otherwise user has many masks with the same name but different extensions
                mask_image_path = masks_found[0]

            mask_image = Image.open(mask_image_path)
            p.image_mask = mask_image

        proc = modules.scripts.scripts_img2img.run(p, *args)
        if proc is None:
            proc = process_images(p)

        for n, processed_image in enumerate(proc.images):
            filename = image_path.name

            if n > 0:
                left, right = os.path.splitext(filename)
                filename = f"{left}-{n}{right}"

            if not save_normally:
                os.makedirs(output_dir, exist_ok=True)
                if processed_image.mode == 'RGBA':
                    processed_image = processed_image.convert("RGB")
                processed_image.save(os.path.join(output_dir, filename))


def img2img(id_task: str, mode: int, prompt: str, negative_prompt: str, 
            prompt_styles, init_img, sketch, init_img_with_mask, inpaint_color_sketch, 
            inpaint_color_sketch_orig, init_img_inpaint, 
            init_mask_inpaint, steps: int, 
            sampler_index: int, 
            mask_blur: int, mask_alpha: float, 
            inpainting_fill: int, 
            restore_faces: bool, 
            tiling: bool, n_iter: int, 
            batch_size: int, 
            cfg_scale: float, image_cfg_scale: float, 
            denoising_strength: float, seed: int, subseed: int, subseed_strength: float, 
            seed_resize_from_h: int, seed_resize_from_w: int, seed_enable_extras: bool, 
            selected_scale_tab: int, height: int, width: int, scale_by: float, 
            resize_mode: int, inpaint_full_res: bool, inpaint_full_res_padding: int, 
            inpainting_mask_invert: int, img2img_batch_input_dir: str, 
            img2img_batch_output_dir: str, img2img_batch_inpaint_mask_dir: str, 
            override_settings_texts, *args):
    override_settings = create_override_settings_dict(override_settings_texts)

    is_batch = mode == 5

    if mode == 0:  # img2img
        image = init_img.convert("RGB")
        mask = None
    elif mode == 1:  # img2img sketch
        image = sketch.convert("RGB")
        mask = None
    elif mode == 2:  # inpaint
        image, mask = init_img_with_mask["image"], init_img_with_mask["mask"]
        alpha_mask = ImageOps.invert(image.split()[-1]).convert('L').point(lambda x: 255 if x > 0 else 0, mode='1')
        mask = mask.convert('L').point(lambda x: 255 if x > 128 else 0, mode='1')
        mask = ImageChops.lighter(alpha_mask, mask).convert('L')
        image = image.convert("RGB")
    elif mode == 3:  # inpaint sketch
        image = inpaint_color_sketch
        orig = inpaint_color_sketch_orig or inpaint_color_sketch
        pred = np.any(np.array(image) != np.array(orig), axis=-1)
        mask = Image.fromarray(pred.astype(np.uint8) * 255, "L")
        mask = ImageEnhance.Brightness(mask).enhance(1 - mask_alpha / 100)
        blur = ImageFilter.GaussianBlur(mask_blur)
        image = Image.composite(image.filter(blur), orig, mask.filter(blur))
        image = image.convert("RGB")
    elif mode == 4:  # inpaint upload mask
        image = init_img_inpaint
        mask = init_mask_inpaint
    else:
        image = None
        mask = None

    # Use the EXIF orientation of photos taken by smartphones.
    if image is not None:
        image = ImageOps.exif_transpose(image)

    if selected_scale_tab == 1 and not is_batch:
        assert image, "Can't scale by because no image is selected"

        width = int(image.width * scale_by)
        height = int(image.height * scale_by)

    assert 0. <= denoising_strength <= 1., 'can only work with strength in [0.0, 1.0]'

    ## 这里确保了, img2img界面里的processing一定是Img2Img
    p = StableDiffusionProcessingImg2Img(
        sd_model=shared.sd_model,
        outpath_samples=opts.outdir_samples or opts.outdir_img2img_samples,
        outpath_grids=opts.outdir_grids or opts.outdir_img2img_grids,
        prompt=prompt,
        negative_prompt=negative_prompt,
        styles=prompt_styles,
        seed=seed,
        subseed=subseed,
        subseed_strength=subseed_strength,
        seed_resize_from_h=seed_resize_from_h,
        seed_resize_from_w=seed_resize_from_w,
        seed_enable_extras=seed_enable_extras,
        sampler_name=sd_samplers.samplers_for_img2img[sampler_index].name,
        batch_size=batch_size,
        n_iter=n_iter,
        steps=steps,
        cfg_scale=cfg_scale,
        width=width,
        height=height,
        restore_faces=restore_faces,
        tiling=tiling,
        init_images=[image],
        mask=mask,
        mask_blur=mask_blur,
        inpainting_fill=inpainting_fill,
        resize_mode=resize_mode,
        denoising_strength=denoising_strength,
        image_cfg_scale=image_cfg_scale,
        inpaint_full_res=inpaint_full_res,
        inpaint_full_res_padding=inpaint_full_res_padding,
        inpainting_mask_invert=inpainting_mask_invert,
        override_settings=override_settings,
    )

    p.scripts = modules.scripts.scripts_img2img
    p.script_args = args

    if shared.cmd_opts.enable_console_prompts:
        print(f"\nimg2img: {prompt}", file=shared.progress_print_out)

    if mask:
        p.extra_generation_params["Mask blur"] = mask_blur

    if is_batch:
        assert not shared.cmd_opts.hide_ui_dir_config, "Launched with --hide-ui-dir-config, batch img2img disabled"

        process_batch(p, img2img_batch_input_dir, img2img_batch_output_dir, img2img_batch_inpaint_mask_dir, args, to_scale=selected_scale_tab == 1, scale_by=scale_by)

        processed = Processed(p, [], p.seed, "")
    else:
        processed = modules.scripts.scripts_img2img.run(p, *args) ## 调用实际script的run()函数
        if processed is None:
            processed = process_images(p)

    p.close()

    shared.total_tqdm.clear()

    generation_info_js = processed.js()
    if opts.samples_log_stdout:
        print(generation_info_js)

    if opts.do_not_show_images:
        processed.images = []

    return processed.images, generation_info_js, plaintext_to_html(processed.info), plaintext_to_html(processed.comments)
