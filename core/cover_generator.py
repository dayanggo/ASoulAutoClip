import os
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter

class CoverGenerator:
    def __init__(self, config):
        self.config = config

    @staticmethod
    def split_title_smartly(title, max_chars=10):
        split_chars = ['!', '!', '?', '?', ',', ',', '。', ':']
        for i, char in enumerate(title):
            if char in split_chars and 3 <= i <= len(title) - 3:
                return title[:i+1], title[i+1:]
        mid = len(title) // 2
        return title[:mid], title[mid:]

    @staticmethod
    def add_image_to_cover(base_img, image_config):
        image_path = image_config.get('path', '')
        if not image_path or not os.path.exists(image_path):
            if image_path:
                print(f"  ⚠️ 图片不存在: {image_path}")
            return base_img
        
        try:
            overlay_img = Image.open(image_path).convert("RGBA")
            target_size = image_config.get('size')
            if target_size:
                overlay_img = overlay_img.resize(target_size, Image.Resampling.LANCZOS)
            
            opacity = image_config.get('opacity', 1.0)
            if opacity < 1.0:
                alpha = overlay_img.split()[3]
                alpha = alpha.point(lambda p: int(p * opacity))
                overlay_img.putalpha(alpha)
            
            base_width, base_height = base_img.size
            img_width, img_height = overlay_img.size
            
            x = image_config.get('x', 0)
            y = image_config.get('y', 0)
            
            if isinstance(x, float) and 0.0 <= x <= 1.0:
                x = int(x * base_width)
            else:
                x = int(x)
            
            if isinstance(y, float) and 0.0 <= y <= 1.0:
                y = int(y * base_height)
            else:
                y = int(y)
            
            anchor = image_config.get('anchor', 'top_left').lower()
            anchor_offsets = {
                'top_left': (0, 0), 'top_center': (-img_width // 2, 0), 'top_right': (-img_width, 0),
                'center_left': (0, -img_height // 2), 'center': (-img_width // 2, -img_height // 2),
                'center_right': (-img_width, -img_height // 2),
                'bottom_left': (0, -img_height), 'bottom_center': (-img_width // 2, -img_height),
                'bottom_right': (-img_width, -img_height)
            }
            offset_x, offset_y = anchor_offsets.get(anchor, (0, 0))
            final_x = x + offset_x
            final_y = y + offset_y
            final_x = max(0, min(final_x, base_width - img_width))
            final_y = max(0, min(final_y, base_height - img_height))
            
            base_img.paste(overlay_img, (final_x, final_y), overlay_img)
        except Exception as e:
            print(f"  ⚠️ 图片叠加失败 ({image_path}): {e}")
        return base_img

    def create_aesthetic_cover(self, video_path, timestamp_sec, cover_text_1, cover_text_2, output_path, style_config, images_list=None):
        temp_img = output_path.with_suffix('.temp.jpg')
        cmd = [
            'ffmpeg', '-ss', str(timestamp_sec), '-i', video_path,
            '-frames:v', '1', '-q:v', '2', '-y', str(temp_img)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not temp_img.exists():
            return

        try:
            img = Image.open(temp_img).convert("RGBA")
            width, height = img.size
            
            if style_config.get('blur_background', False):
                img = img.filter(ImageFilter.GaussianBlur(style_config.get('blur_radius', 3)))
            
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            gradient_start_y = int(height * style_config.get('gradient_start_y', 0.6))
            gradient_opacity = style_config.get('gradient_opacity', 200)
            for y in range(gradient_start_y, height):
                progress = (y - gradient_start_y) / (height - gradient_start_y)
                alpha = int(progress * gradient_opacity)
                draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
            
            try:
                font_path = self.config['font_path']
                title_font = ImageFont.truetype(font_path, style_config['title_size'])
            except IOError:
                title_font = ImageFont.load_default()

            layout = style_config.get('layout', 'bottom')
            
            def draw_text_with_multilayer_stroke(draw, position, text, font, fill_color, stroke_color, stroke_width):
                x, y = position
                canvas = draw._image
                canvas_width, canvas_height = canvas.size
                
                text_layer = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
                text_draw = ImageDraw.Draw(text_layer)
                text_draw.text((x, y), text, font=font, fill=fill_color, anchor="mm")
                
                stroke_layer = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
                stroke_draw = ImageDraw.Draw(stroke_layer)
                stroke_draw.text((x, y), text, font=font, fill=stroke_color, anchor="mm")
                
                for _ in range(stroke_width):
                    stroke_layer = stroke_layer.filter(ImageFilter.MaxFilter(3))
                
                canvas.paste(stroke_layer, (0, 0), stroke_layer)
                canvas.paste(text_layer, (0, 0), text_layer)
            
            if layout == "double" and style_config.get('title_position') == "split":
                y1 = int(height * style_config.get('title_top_y_ratio', 0.2))
                top_color = style_config.get('title_top_color', style_config.get('title_color', (255, 255, 255)))
                draw_text_with_multilayer_stroke(
                    draw, (width / 2, y1), cover_text_1, title_font,
                    top_color, style_config['title_stroke_color'], style_config['title_stroke_width']
                )
                if cover_text_2:
                    y2 = int(height * style_config.get('title_bottom_y_ratio', 0.75))
                    bottom_color = style_config.get('title_bottom_color', style_config.get('title_color', (255, 255, 255)))
                    draw_text_with_multilayer_stroke(
                        draw, (width / 2, y2), cover_text_2, title_font,
                        bottom_color, style_config['title_stroke_color'], style_config['title_stroke_width']
                    )
            else:
                title_y = int(height * style_config.get('title_y_ratio', 0.85))
                text_width = draw.textlength(cover_text_1, font=title_font)
                if text_width > width - 100:
                    scaled_size = int(style_config['title_size'] * (width - 100) / text_width)
                    title_font = ImageFont.truetype(font_path, scaled_size)
                
                draw_text_with_multilayer_stroke(
                    draw, (width / 2, title_y), cover_text_1, title_font,
                    style_config['title_color'], style_config['title_stroke_color'], style_config['title_stroke_width']
                )

            img = Image.alpha_composite(img, overlay)
            
            images_list = images_list or []
            if images_list:
                for image_config in images_list:
                    if isinstance(image_config, dict):
                        img = CoverGenerator.add_image_to_cover(img, image_config)

            final_img = img.convert('RGB')
            final_img = final_img.filter(ImageFilter.SHARPEN)
            final_img.save(output_path, quality=95)
        except Exception as e:
            print(f"⚠️ 封面生成失败: {e}")
        finally:
            if temp_img.exists():
                temp_img.unlink()

    def create_multiple_covers(self, video_path, start_sec, end_sec, cover_text_1, cover_text_2, base_output_path, cover_count, cover_config=None):
        duration = end_sec - start_sec
        if cover_count <= 0:
            return []
        if not cover_config:
            cover_config = self.config.get('cover')
        if not cover_config:
            return []

        active_style = cover_config.get('active_style', 'style1')
        style_config = cover_config.get(active_style, {})
        images_list = cover_config.get('images', [])
        
        print(f"   使用封面样式: {style_config.get('name', active_style)}")
        
        if cover_count == 1:
            positions = [0.5]
        else:
            positions = [0.2 + (0.6 / (cover_count - 1)) * i for i in range(cover_count)]
        
        generated_covers = []
        for i, pos in enumerate(positions, 1):
            timestamp = start_sec + duration * pos
            output_path = base_output_path.parent / f"{base_output_path.stem}_cover{i}{base_output_path.suffix}"
            self.create_aesthetic_cover(
                video_path, timestamp, cover_text_1, cover_text_2, output_path, style_config, images_list
            )
            if output_path.exists():
                generated_covers.append(output_path)
        
        if generated_covers:
            print(f"   已生成 {len(generated_covers)} 张封面")
        return generated_covers
