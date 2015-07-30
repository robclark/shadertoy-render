#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2015, Alex J. Champandard
# Copyright (c) 2015, Vispy Development Team.
# Copyright (c) 2015, Rob Clark
#
# Distributed under the (new) BSD License.

from __future__ import (unicode_literals, print_function)

import sys
import argparse
import datetime
import subprocess

import numpy

import vispy
from vispy import gloo
from vispy import app

import os
import requests
import imageio
import urllib.request, urllib.parse
import json

url = 'https://www.shadertoy.com/api/v1/shaders'
key = '?key=NdnKw7'


vertex = """
#version 120

attribute vec2 position;
void main()
{
    gl_Position = vec4(position, 0.0, 1.0);
}
"""

fragment = """
#version 120

uniform vec3      iResolution;           // viewport resolution (in pixels)
uniform float     iGlobalTime;           // shader playback time (in seconds)
uniform vec4      iMouse;                // mouse pixel coords
uniform vec4      iDate;                 // (year, month, day, time in seconds)
uniform float     iSampleRate;           // sound sample rate (i.e., 44100)
uniform vec3      iChannelResolution[4]; // channel resolution (in pixels)
uniform float     iChannelTime[4];       // channel playback time (in sec)
%s

%s

void main()
{
    mainImage(gl_FragColor, gl_FragCoord.xy);
}
"""


def get_idate():
    now = datetime.datetime.now()
    utcnow = datetime.datetime.utcnow()
    midnight_utc = datetime.datetime.combine(utcnow.date(), datetime.time(0))
    delta = utcnow - midnight_utc
    return (now.year, now.month, now.day, delta.seconds)


class RenderingCanvas(app.Canvas):

    def __init__(self, renderpass, size=None, rate=30.0, duration=None):
        app.Canvas.__init__(self, keys='interactive', size=size, title='ShaderToy Renderer')

        # Figure out our up-to-four inputs:
        samplers = ""
        for input in renderpass['inputs']:
            #print(str(input))
            t = input['ctype']
            chan = input['channel'];
            if t == "texture":
                samp = "sampler2D"
            elif t == "cubemap":
                samp = "samplerCube"
            elif t == "music":
                # skip
                continue
            else:
                raise Exception("Unknown sampler type: %s" % t)
            samplers = samplers + ("\nuniform %s iChannel%d;" % (samp, chan))

        glsl = fragment % (samplers, renderpass['code'])
        #print(glsl)
        self.program = gloo.Program(vertex, glsl)
        self.program["position"] = [(-1, -1), (-1, 1), (1, 1), (-1, -1), (1, 1), (1, -1)]
        self.program['iMouse'] = 0.0, 0.0, 0.0, 0.0
        self.program['iSampleRate'] = 44100.0

        for i in range(4):
            self.program['iChannelTime[%d]' % i] = 0.0
        self.program['iGlobalTime'] = 0.0

        self.activate_zoom()

        self._rate = rate
        self._duration = duration
        self._timer = app.Timer('auto', connect=self.on_timer, start=True)

        # Fetch and setup input textures:
        for input in renderpass['inputs']:
            t    = input['ctype']
            chan = input['channel']
            src  = input['src']
            print("Fetching texture: %s" % src)
            if t == "texture":
                img = imageio.imread("https://www.shadertoy.com/%s" % src)
                tex = gloo.Texture2D(img)
            elif t == "cubemap":
                # NOTE: cubemap textures, the src seems to give only the first
                # face, ie. cube04_0.png, and we have to infer cube04_1.png,
                # to cube04_5.png for the remaining faces..
                raise Exception("TODO: TextureCubeMap not implemented!")
            elif t == "music":
                # skip
                continue
            tex.interpolation = 'linear'
            tex.wrapping = 'repeat'
            self.program['iChannel%d' % chan] = tex
            self.program['iChannelResolution[%d]' % chan] = img.shape

        # TODO this doesn't seem to work with python3
        #self.size = (size[0] / self.pixel_scale, size[1] / self.pixel_scale)
        self.show()

    def on_draw(self, event):
        self.program['iGlobalTime'] += 1.0 / self._rate
        self.program.draw()

        if self._duration is not None and self.program['iGlobalTime'] >= self._duration:
            app.quit()

    def on_mouse_click(self, event):
        imouse = event.pos + event.pos
        self.program['iMouse'] = imouse

    def on_mouse_move(self, event):
        if event.is_dragging:
            x, y = event.pos
            px, py = event.press_event.pos
            imouse = (x, self.size[1] - y, px, self.size[1] - py)
            self.program['iMouse'] = imouse

    def on_timer(self, event):
        self.update()

    def on_resize(self, event):
        self.activate_zoom()

    def activate_zoom(self):
        gloo.set_viewport(0, 0, *self.physical_size)
        self.program['iResolution'] = (self.physical_size[0], self.physical_size[1], 0.)


if __name__ == '__main__':
    vispy.set_log_level('WARNING')
    vispy.use(app='glfw')

    parser = argparse.ArgumentParser(description='Render a ShaderToy script.')
    parser.add_argument('id', type=str, help='Shadertoy shader id.')
    parser.add_argument('--rate', type=int, default=30, help='Number of frames per second to render, e.g. 60 (int).')
    parser.add_argument('--duration', type=float, default=None, help='Total seconds of video to encode, e.g. 30.0 (float).')
    parser.add_argument('--size', type=str, default='1280x720', help='Width and height of the rendering, e.g. 1920x1080 (string).')
    args = parser.parse_args()
    
    resolution = [int(i) for i in args.size.split('x')]

    print('Fetching shader: {}'.format(args.id))

    # For some reason, this doesn't always work, so if needed try a
    # different way:
    try:
        r = requests.get(url + '/' + args.id + key)
        j = r.json()
        s = j['Shader']
    except KeyError:
        alt_url = 'https://www.shadertoy.com/shadertoy'
        headers = { 'Referer' : 'https://www.shadertoy.com/' }
        values  = { 's' : json.dumps ({'shaders' : [args.id]}) }

        data = urllib.parse.urlencode (values).encode ('utf-8')
        req  = urllib.request.Request (alt_url, data, headers)
        response = urllib.request.urlopen (req)
        shader_json = response.read ().decode ('utf-8')
        j = json.loads (shader_json)
        s = j[0]

    info = s['info']
    print('Name: ' + info['name'])
    print('Description: ' + info['description'])
    print('Author: ' + info['username'])

    # first renderpass seems to always be video (and second is audio if present.. we'll skip that..)
    renderpass = s['renderpass'][0]

    canvas = RenderingCanvas(renderpass,
                             size=resolution,
                             rate=args.rate,
                             duration=args.duration)

    try:
        canvas.app.run()
    except KeyboardInterrupt:
        pass
