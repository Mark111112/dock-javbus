#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
from PIL import Image, ImageDraw, ImageFont

def create_placeholder_image():
    """Create a placeholder image for movie covers"""
    # Create the static/images directory if it doesn't exist
    os.makedirs("static/images", exist_ok=True)
    
    # Image dimensions
    width, height = 480, 720
    
    # Create a blank image with gray background
    img = Image.new('RGB', (width, height), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    
    # Draw a border
    border_color = (200, 200, 200)
    border_width = 2
    draw.rectangle(
        [(border_width, border_width), (width - border_width, height - border_width)],
        outline=border_color, width=border_width
    )
    
    # Add text
    try:
        # Try to use a common font
        font = ImageFont.truetype("arial.ttf", 36)
    except IOError:
        # Fall back to default font
        font = ImageFont.load_default()
    
    text = "No Cover Available"
    text_width = font.getlength(text) if hasattr(font, 'getlength') else font.getsize(text)[0]
    text_position = ((width - text_width) // 2, height // 2)
    draw.text(text_position, text, fill=(100, 100, 100), font=font)
    
    # Add a film icon or placeholder symbol
    icon_size = 100
    icon_position = ((width - icon_size) // 2, (height // 2) - 120)
    draw.rectangle(
        [icon_position, (icon_position[0] + icon_size, icon_position[1] + icon_size)],
        outline=(150, 150, 150), width=2, fill=(220, 220, 220)
    )
    
    # Draw a film reel symbol inside the square
    center_x = icon_position[0] + icon_size // 2
    center_y = icon_position[1] + icon_size // 2
    draw.ellipse(
        [(center_x - 30, center_y - 30), (center_x + 30, center_y + 30)],
        outline=(150, 150, 150), width=2
    )
    
    # Save the image
    output_path = "static/images/no-cover.jpg"
    img.save(output_path, "JPEG", quality=90)
    print(f"Created placeholder image: {output_path}")

if __name__ == "__main__":
    create_placeholder_image() 