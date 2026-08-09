future enchancements
1) DONE - add a 'global' section above the 3rd column (above 'audio diagnostics'). include a 'master audio' slider, and move the VU meter to this section, making these both vertical outputs and UI. include a 'Disable Audio' and and a 'Disable visuals' buttons (these both have a 2 second fade out). include the 'scene transitions' section in this section too - but remove the support text for this.
2)DONE-  add a new section 'Scene settings' under the new 'global' section in column 3. Add the 'Save Soundscape settings' button here, add a 'Save Camera settings' and a 'Save all scene settings' and a 'Save as' button which allows provides a modal popup and to specify a new name and saves the current scene config into it. add a then remove the 'Save soundscape' button from the primary soundscape section. remove everything under the 'generate scene' button in the scene section in the last comlumn in the last row.
3)DONE -  in the header area, change the indicator approach to this "Connections - [indicator light] Engine - [indicator light] Claude API" - so that the backend engine and API are simply shown.
4)DONE add a oscilator mode (waveform options - sine etc, with effect) that can map to 2 options, a) the XXXXX a dropdown of objects/shapes inthe scene's json file - so that they could be scaled/morphed inde
5) add a 'hue' override in the global section. this overrides the color of the output's scene
7)DONE add a 'FPS" setting per project, still seems low in output (19fps)
8) DONE when collasping the 'imput' sidebar, collapse all subsections inside it
9) DONE add a 'lock' to a shape, )via shape settings) so that you can't easy edit it in the canvas, and show padlock icon in shapelist.
10) DONE update shape buttons in shape list so that whole button is clickable, not just text inside button.
11) DONE tidy up project pane, break sections into vertical panes.
12) DONE improve 'not yet saved' inidactor, maybe needs better UX approach, as not very obious, maybe a sticky hovering thin panel at bottom right of window??
13) DONEmove the 'add shape' to the bothom of hte 'shape list' 
14) DONE remove the words from the tools (select/hand)
15) DONE add a video media playbook filter, eg in/out points, playback(loop,ping-pong,once)
17) DONE simplify readme and move other stuff into technical.md
18) DONE - review appraoch of using project resolution for new shapes or inserted shapes, very stretched - looks terrible for text, media etc.for media it should use the media's ratio. for text maybe 1:1?
19) DONE - allow poly/shape points to extend past the canvas boundaries if possible, so can drag closer shape to edge, as currenlty unable to move a point past the bounds, so can't move a distorted shape right to the edge of the canvas.
20) DONE - if no shape is selected , choose the first shape in the list, and show the 'shape settings' pane open
21) DONE - move the 'media' playback, in/out , trim etc to its own pane, (from the 'shape settings' pane) and have a button for this in the last column
22) DONE - move the 'clip shape' options etc to its own pane, (from the 'shape settings' pane) and have a button for this in the last column titled 'Mask/clipping', and change button label from 'warp' to 'distort'.
23) DONE - reduce width of 'shapes list' column by 30%
24) DONE - move the last column "shape settings, colourize, etc" after the 'shapes list' column, and replace words with icons (with tooltips for the words tho), and make this quite narrow, just wider than the columns and wrap into the left side, so its visually tied to the settings for each of the new icons.
25) DONE - add an 'about.md' file and a new modal with a '?' icon in the header after the settings, fill this file with a very short explaination and link back to the github repo.