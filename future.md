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
26) done use the webcam's native resolution and dimensions when adding a new webcam shape to the canvas
27) done - add more horuzronal padding between the 'shape list' and the next column, as the icons are overlapping the shape list, needs a visual diffentiaion 
28) done - on the canvas, if i shift drag the corners of a shape it shoudl scale proportioanlly in all directions.
29)done -  on the 'media' tab - the two sliders dont allow the mouse to be dragged in realtime, its a click to action, ideally this could be a realtime playback scrubber, if you click and drag it moves the video etc, like a normal playback bar
30) done -  cant see jpg imaes in preview within 'media' tab.
31) done-  'upload a copy' in the media tab doesnt work
32) done-ish - ideally for the 'absolute path' this could be an option to chooose a folder using the computers native file/folder picker - and change the button/language to 'Media folder' - and i assume this is per-project too - so a diff project can use a diffeent 'media folder'? this section (link media/media root) is a bit ambigious and needs to be plain langauge.
33) done - all modals should be able to be closed with the 'escape' key
34) DONE - download the bootstrap icons and replace emojis with relevant options using the bootstrap ones instead.
35) remove/fix terminal errors when quitting the process using 'control c'
36) DONE - FPS is still not fixed as close as possibel to projects settings - witho both outpyut windows opening its goign up to 200fps and getting bad performance. 
37) DONE - fix the spacing between the 'shapes list' and the 'shape settings' columns, theres a vertical line thats being overlapped. 
38) DONE - add a H2 called 'Shape settings' above the last column, so its simlar to the 'shapes' title in the shapes list.
39) DONE - add a dim overlay line on the canvas where the output crops are, so i can see where the 'cut' is set.
40) DONE - 'text' shapes aren't drawn correcly when they span across two outputs. you can see this on the doublehappy768 - wide1 canvas, the 'hello' text appears incorrectly on the 2 outputs.