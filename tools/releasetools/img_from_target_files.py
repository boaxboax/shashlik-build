#!/usr/bin/env python
#
# Copyright (C) 2008 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Given a target-files zipfile, produces an image zipfile suitable for
use with 'fastboot update'.

Usage:  img_from_target_files [flags] input_target_files output_image_zip

  -b  (--board_config)  <file>
      Deprecated.

  -z  (--bootable_zip)
      Include only the bootable images (eg 'boot' and 'recovery') in
      the output.

"""

import sys

if sys.hexversion < 0x02070000:
  print >> sys.stderr, "Python 2.7 or newer is required."
  sys.exit(1)

import errno
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

# missing in Python 2.4 and before
if not hasattr(os, "SEEK_SET"):
  os.SEEK_SET = 0

import build_image
import common

OPTIONS = common.OPTIONS


def AddSystem(output_zip, sparse=True):
  """Turn the contents of SYSTEM into a system image and store it in
  output_zip."""
  data = BuildSystem(OPTIONS.input_tmp, OPTIONS.info_dict, sparse=sparse)
  common.ZipWriteStr(output_zip, "system.img", data)

def BuildSystem(input_dir, info_dict, sparse=True, map_file=None):
  return CreateImage(input_dir, info_dict, "system",
                     sparse=sparse, map_file=map_file)

def AddVendor(output_zip, sparse=True):
  data = BuildVendor(OPTIONS.input_tmp, OPTIONS.info_dict, sparse=sparse)
  common.ZipWriteStr(output_zip, "vendor.img", data)

def BuildVendor(input_dir, info_dict, sparse=True, map_file=None):
  return CreateImage(input_dir, info_dict, "vendor",
                     sparse=sparse, map_file=map_file)


def CreateImage(input_dir, info_dict, what, sparse=True, map_file=None):
  print "creating " + what + ".img..."

  img = tempfile.NamedTemporaryFile()

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  It wants "system" but we have a directory named
  # "SYSTEM", so create a symlink.
  try:
    os.symlink(os.path.join(input_dir, what.upper()),
               os.path.join(input_dir, what))
  except OSError, e:
      # bogus error on my mac version?
      #   File "./build/tools/releasetools/img_from_target_files", line 86, in AddSystem
      #     os.path.join(OPTIONS.input_tmp, "system"))
      # OSError: [Errno 17] File exists
    if (e.errno == errno.EEXIST):
      pass

  image_props = build_image.ImagePropFromGlobalDict(info_dict, what)
  fstab = info_dict["fstab"]
  if fstab:
    image_props["fs_type" ] = fstab["/" + what].fs_type

  if what == "system":
    fs_config_prefix = ""
  else:
    fs_config_prefix = what + "_"

  fs_config = os.path.join(
      input_dir, "META/" + fs_config_prefix + "filesystem_config.txt")
  if not os.path.exists(fs_config): fs_config = None

  fc_config = os.path.join(input_dir, "BOOT/RAMDISK/file_contexts")
  if not os.path.exists(fc_config): fc_config = None

  succ = build_image.BuildImage(os.path.join(input_dir, what),
                                image_props, img.name,
                                fs_config=fs_config,
                                fc_config=fc_config)
  assert succ, "build " + what + ".img image failed"

  mapdata = None

  if sparse:
    data = open(img.name).read()
    img.close()
  else:
    success, name = build_image.UnsparseImage(img.name, replace=False)
    if not success:
      assert False, "unsparsing " + what + ".img failed"

    if map_file:
      mmap = tempfile.NamedTemporaryFile()
      mimg = tempfile.NamedTemporaryFile(delete=False)
      success = build_image.MappedUnsparseImage(
          img.name, name, mmap.name, mimg.name)
      if not success:
        assert False, "creating sparse map failed"
      os.unlink(name)
      name = mimg.name

      with open(mmap.name) as f:
        mapdata = f.read()

    try:
      with open(name) as f:
        data = f.read()
    finally:
      os.unlink(name)

  if mapdata is None:
    return data
  else:
    return mapdata, data


def AddUserdata(output_zip):
  """Create an empty userdata image and store it in output_zip."""

  image_props = build_image.ImagePropFromGlobalDict(OPTIONS.info_dict,
                                                    "data")
  # We only allow yaffs to have a 0/missing partition_size.
  # Extfs, f2fs must have a size. Skip userdata.img if no size.
  if (not image_props.get("fs_type", "").startswith("yaffs") and
      not image_props.get("partition_size")):
    return

  print "creating userdata.img..."

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  So we create a temp dir, and within it we create an
  # empty dir named "data", and build the image from that.
  temp_dir = tempfile.mkdtemp()
  user_dir = os.path.join(temp_dir, "data")
  os.mkdir(user_dir)
  img = tempfile.NamedTemporaryFile()

  fstab = OPTIONS.info_dict["fstab"]
  if fstab:
    image_props["fs_type" ] = fstab["/data"].fs_type
  succ = build_image.BuildImage(user_dir, image_props, img.name)
  assert succ, "build userdata.img image failed"

  common.CheckSize(img.name, "userdata.img", OPTIONS.info_dict)
  output_zip.write(img.name, "userdata.img")
  img.close()
  os.rmdir(user_dir)
  os.rmdir(temp_dir)


def AddCache(output_zip):
  """Create an empty cache image and store it in output_zip."""

  image_props = build_image.ImagePropFromGlobalDict(OPTIONS.info_dict,
                                                    "cache")
  # The build system has to explicitly request for cache.img.
  if "fs_type" not in image_props:
    return

  print "creating cache.img..."

  # The name of the directory it is making an image out of matters to
  # mkyaffs2image.  So we create a temp dir, and within it we create an
  # empty dir named "cache", and build the image from that.
  temp_dir = tempfile.mkdtemp()
  user_dir = os.path.join(temp_dir, "cache")
  os.mkdir(user_dir)
  img = tempfile.NamedTemporaryFile()

  fstab = OPTIONS.info_dict["fstab"]
  if fstab:
    image_props["fs_type" ] = fstab["/cache"].fs_type
  succ = build_image.BuildImage(user_dir, image_props, img.name)
  assert succ, "build cache.img image failed"

  common.CheckSize(img.name, "cache.img", OPTIONS.info_dict)
  output_zip.write(img.name, "cache.img")
  img.close()
  os.rmdir(user_dir)
  os.rmdir(temp_dir)


def CopyInfo(output_zip):
  """Copy the android-info.txt file from the input to the output."""
  output_zip.write(os.path.join(OPTIONS.input_tmp, "OTA", "android-info.txt"),
                   "android-info.txt")


def main(argv):
  bootable_only = [False]

  def option_handler(o, a):
    if o in ("-b", "--board_config"):
      pass       # deprecated
    if o in ("-z", "--bootable_zip"):
      bootable_only[0] = True
    else:
      return False
    return True

  args = common.ParseOptions(argv, __doc__,
                             extra_opts="b:z",
                             extra_long_opts=["board_config=",
                                              "bootable_zip"],
                             extra_option_handler=option_handler)

  bootable_only = bootable_only[0]

  if len(args) != 2:
    common.Usage(__doc__)
    sys.exit(1)

  OPTIONS.input_tmp, input_zip = common.UnzipTemp(args[0])
  OPTIONS.info_dict = common.LoadInfoDict(input_zip)

  # If this image was originally labelled with SELinux contexts, make sure we
  # also apply the labels in our new image. During building, the "file_contexts"
  # is in the out/ directory tree, but for repacking from target-files.zip it's
  # in the root directory of the ramdisk.
  if "selinux_fc" in OPTIONS.info_dict:
    OPTIONS.info_dict["selinux_fc"] = os.path.join(OPTIONS.input_tmp, "BOOT", "RAMDISK",
        "file_contexts")

  output_zip = zipfile.ZipFile(args[1], "w", compression=zipfile.ZIP_DEFLATED)

  boot_image = common.GetBootableImage(
      "boot.img", "boot.img", OPTIONS.input_tmp, "BOOT")
  if boot_image:
      boot_image.AddToZip(output_zip)
  recovery_image = common.GetBootableImage(
      "recovery.img", "recovery.img", OPTIONS.input_tmp, "RECOVERY")
  if recovery_image:
    recovery_image.AddToZip(output_zip)

  def banner(s):
    print "\n\n++++ " + s + " ++++\n\n"

  if not bootable_only:
    banner("AddSystem")
    AddSystem(output_zip)
    try:
      input_zip.getinfo("VENDOR/")
      banner("AddVendor")
      AddVendor(output_zip)
    except KeyError:
      pass   # no vendor partition for this device
    banner("AddUserdata")
    AddUserdata(output_zip)
    banner("AddCache")
    AddCache(output_zip)
    CopyInfo(output_zip)

  print "cleaning up..."
  output_zip.close()
  shutil.rmtree(OPTIONS.input_tmp)

  print "done."


if __name__ == '__main__':
  try:
    common.CloseInheritedPipes()
    main(sys.argv[1:])
  except common.ExternalError, e:
    print
    print "   ERROR: %s" % (e,)
    print
    sys.exit(1)
