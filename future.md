future enchancements
1) DONE - add a 'global' section above the 3rd column (above 'audio diagnostics'). include a 'master audio' slider, and move the VU meter to this section, making these both vertical outputs and UI. include a 'Disable Audio' and and a 'Disable visuals' buttons (these both have a 2 second fade out). include the 'scene transitions' section in this section too - but remove the support text for this.
2)DONE-  add a new section 'Scene settings' under the new 'global' section in column 3. Add the 'Save Soundscape settings' button here, add a 'Save Camera settings' and a 'Save all scene settings' and a 'Save as' button which allows provides a modal popup and to specify a new name and saves the current scene config into it. add a then remove the 'Save soundscape' button from the primary soundscape section. remove everything under the 'generate scene' button in the scene section in the last comlumn in the last row.
3)DONE -  in the header area, change the indicator approach to this "Connections - [indicator light] Engine - [indicator light] Claude API" - so that the backend engine and API are simply shown.
4)DONE add a oscilator mode (waveform options - sine etc, with effect) that can map to 2 options, a) the XXXXX a dropdown of objects/shapes inthe scene's json file - so that they could be scaled/morphed inde
that you can't easy edit it in the canvas, and show padlock icon in shapelist.
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
41) DONE - add the 'test' image option per output as a checkbox on the 'output preview' panel, so its quick to setup/enable and dsiable.
42) DONE - 'knockout' - feel that this shoudl be its own 'input' as its hidden in the shape options - also the name annoys me, its a mask of sorts, even if its not a mask on a shape, its a mask shape. suggest if we should change it to 'mask' or not. is this approach right?
43) DONE - split out the option of 'custom ploygon' from the 'clip shape' dropdown, its pretty common function and needs to be obviois.
44) DONE - on the 'clip/mask' pane when a clip shape is set (circle, hex etc) it uses the shapes ratio and streches the clip shape, so its often distorted. can we have an checkbox here which set its proportiaonlly to the shapes dimensions/ratio or excludes the dimensions so its truly round etc.?
45) DONE - add another button in the cavnas header for 'show/hide z-index' of shapes in the canvas - use the 'front' BS icon.
46) DONE - collapse the 'input' drawer if theres clicks out of that pane.
47) bezier mask/clip/cutouts - how expensive are these and complicated to have as an option?? assess and ask before doing anything
48) DONE - the handles on points on shapes within the canvas, as you zoom they get bigger, can they be propotioinal, ie: same size regardless of zoom depth?
49) DONE - the 'fill type' dropdown list order should match the 'inputs' order.
50) DONE - remove the 'focussed editing'  and 'reset zoom/pan' buttons from the canvas.
51) DONE - when creating a new canvas, do not make any shapes initally, let the user create them.
52) DONE - when adding media, provide an option to choose from the 'media library'
53) DONE - in the LFO: we need a min/max option or some sort of range adjuster, as when using a sine wave for opacity, we only want postive values, not negaitve values
54) if appropiate - can you build some solid regresion testing scripts that can be run as i futher develop this project, and save them into a new 'test scripts' folder, this is so me, you and others can run them after new functionailty, and escpcailly for the render host when that progresses. these shoudl test performance, UX and other things you've been rtesting. make sure that the prompts can be useful for humans and AI when priidng feedback from running these.
55) for sliders than have a 'normal' halfway or '1' point (eg rotate,mirror, speed, brightness etc) make a snap point there when dragging, or a very small 'reset' icon at the end of the slider