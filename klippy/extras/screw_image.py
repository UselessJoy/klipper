import math
from . import probe
import locales
from PIL import Image, ImageOps, ImageDraw, ImageFont
import os
import logging
class ScrewImage:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.image_created = False
        self.printer.register_event_handler("screw_tilt_adjust:end_probe", self.create_image)
        self.gcode.register_command(
            "GET_SCREW_IMAGE", self.cmd_GET_SCREW_IMAGE)

    def create_image(self, results):  
        screw_image_file_path = os.path.dirname(__file__)
                
        #Creating directories names
        klippy_dir = os.path.normpath(os.path.join(screw_image_file_path, ".."))
        klipper_dir = os.path.normpath(os.path.join(klippy_dir, ".."))
        config_file_path_name = self.printer.get_start_args()['config_file']
        config_dir = os.path.normpath(os.path.join(config_file_path_name, ".."))
        #images_dir = os.path.join(klippy_dir, "images")
         
        #Fill screw image
        base = Image.new(mode="RGB", size=[600, 600], color=0)
        corner = Image.open(os.path.join(klippy_dir, "images/corner.jpg"))
        base_arrow = Image.open(os.path.join(klippy_dir, "images/base_arrow.png"))
        (width, height) = (base.width // 2, base.height // 2)
        im_corner_resized = corner.resize((width, height))
        arrow_resized = base_arrow.resize((int(width // 2.2), int(height // 4.2)))
        # Add corners
        base.paste(im_corner_resized, [0, 0])
        base.paste(ImageOps.mirror(im_corner_resized), [width, 0])
        base.paste(im_corner_resized.rotate(180, expand=True), [width, height])
        base.paste(ImageOps.mirror(im_corner_resized.rotate(180, expand=True)), [0, height])
        
        # Add arrows
        if results["screw2"]['sign'] == "CW": # front right
            base.paste(arrow_resized, [int(base.width // 1.6), int(base.height // 4)])
        else:
            base.paste(ImageOps.mirror(arrow_resized), [int(base.width // 1.6), int(base.height // 4)])
        if results["screw3"]['sign'] == "CW": # back left
            base.paste(arrow_resized, [int(width // 3.7), int(base.height // 1.4)])
        else:
            base.paste(ImageOps.mirror(arrow_resized), [int(width // 3.7), int(base.height // 1.4)])
        if results["screw4"]['sign'] == "CW": # back right
            base.paste(arrow_resized, [int(base.width // 1.6), int(base.height // 1.4)])
        else:
            base.paste(ImageOps.mirror(arrow_resized), [int(base.width // 1.6), int(base.height // 1.4)])
        screw_image_name = "screw_image.png"
        #add text to screw
        draw_text = ImageDraw.Draw(base)
        fnt = ImageFont.truetype("Pillow/Tests/fonts/FreeMono.ttf", 25)
        #front left
        draw_text.text(
            (int(base.width // 5), int(base.height // 6)),
            "base screw",
            font=fnt,
            fill='#1C0606')
        #front right
        draw_text.text(
            (int(base.width // 1.7), int(base.height // 6)),
            str(results["screw2"]["sign"] + " " + results["screw3"]["adjust"]),
            font=fnt,
            fill='#1C0606')
        #back left
        draw_text.text(
            (int(base.width // 5), int(base.height // 1.6)),
            str(results["screw3"]["sign"] + " " +  results["screw2"]["adjust"]),
            font=fnt,
            fill='#1C0606')
        #back right
        draw_text.text(
            (int(base.width // 1.7), int(base.height // 1.6)),
            str(results["screw4"]["sign"] + " " + results["screw3"]["adjust"]),
            font=fnt,
            fill='#1C0606')
        #save screw image in extras directory in .png format
        base.save(screw_image_name, 'png')
        #create ended directories
        image_path = os.path.join(klipper_dir, screw_image_name)
        fluidd_screw_dir = os.path.join(config_dir, ".fluidd-screw-image/")
        #copy screw image to fluidd screw directory for adding this image in fluidd
        #if directory has any file - delete
        if os.path.isdir(fluidd_screw_dir):
            filelist = os.listdir(fluidd_screw_dir)
            if len(filelist) != 0:
                for file in filelist:
                    os.system('rm ' + fluidd_screw_dir + file)
                    #logging.info(fluidd_screw_dir + file)
        else:
            os.mkdir(fluidd_screw_dir)
        os.system("cp " + image_path + " " + fluidd_screw_dir)
        os.system("rm " + image_path)
        self.image_created = True
        
    def cmd_GET_SCREW_IMAGE(self, gcmd):
        return self.image_created   
    
    def get_status(self, eventtime):
        return {
            'imageCreated': self.image_created
        }
        







def load_config(config):
    return ScrewImage(config)
        