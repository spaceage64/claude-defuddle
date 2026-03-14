---
tags:
  - defuddle/video
  - playstation-1
  - game-development
  - retro-gaming
  - yume-nikki
  - homebrew
  - cplusplus
  - 3d-modeling
  - game-engine
created: 2026-03-14
title: "I made a real PS1 game in 1.5 months"
description: "Developer Elias Daler shares the technical process and challenges of creating a polished, 3D Yume Nikki fan remake for the original PlayStation console."
url: "https://youtu.be/dsA2sQ-rThU"
author: "Elias Daler"
published: "2026-02-02"
site: "YouTube"
duration: "43:23"
words: 6397
---

---

# I made a real PS1 game in 1.5 months

## Summary

Elias Daler details his experience developing 'Yume Nikki PS,' a 3D PlayStation 1 demo, within 1.5 months. He explains the technical hurdles of retro console development, including using C++ with the PSYQo SDK, memory management, and crafting custom asset pipelines for 2MB RAM constraints. The project grew from a small engine test into a viral success, gaining acclaim from prominent streamers like Vinny Vinesauce and Joel. Daler reflects on the challenges of balancing authentic aesthetic recreation with hardware limitations, the surprise of community reception, and the process of later porting the project to PC and browser, concluding that the experience significantly improved his skills as a game developer.

## Description

![I made a real PS1 game in 1.5 months](https://youtu.be/dsA2sQ-rThU)

In this video, you'll learn how I make PS1 games and how Yume Nikki PS (demo) was developed in just 1.5 months.

Twitter: https://twitter.com/EliasDaler
Game: https://eliasdaler.itch.io/yume-nikki-ps1

There's a lot of interesting stuff discussed here, I feel like this is my best work to date.

The video took a month to make. I hope you enjoy it.

Chapters

## Contents

- [[#Intro]]
- [[#Developing PS1 Games]]
- [[#Yume Nikki PS Development Start]]
- [[#Block World]]
- [[#Snow World]]
- [[#Final Polish]]
- [[#The Reaction]]

---

## Transcript

### Intro

[**0:01**](https://youtu.be/dsA2sQ-rThU?t=1) A while ago, I had an idea. What if I remake Yume Nikki in 3D and not just  make a PC game, but make it work on PlayStation 1? And so I did. And in November 2025, I released  a small demo called Yume Nikki PS,   which you can download on itch.io. And this little demo got a lot more attention  and praise than I could ever anticipate. In this video, I'll tell you how  I develop PS1 games and how I

[**0:32**](https://youtu.be/dsA2sQ-rThU?t=32) made a polished and complex demo of Yume  Nikki PS in just one and a half months. For one and a half years now, I've been  developing a game which runs on PS1. The game I was developing was  more of a test for my engine   and not a serious project with real content yet.

### Developing PS1 Games

[**1:03**](https://youtu.be/dsA2sQ-rThU?t=63) Sorry, Morshu fans, he  won't be in the real game... For quite some time, I was feeling a bit anxious  about starting to work on the real game content and I wanted to make something small,   but very polished with my engine to see  if it can be used to make real games. I was a fan of Yume Nikki for a long time and for   years I dreamed of remaking a  portion of it in 3D for fun. So I thought, why not do  it as a small test project? And that's how the development  of Yume Nikki PS started...

[**1:38**](https://youtu.be/dsA2sQ-rThU?t=98) But wait, you might ask, how the  HELL do you make games for PS1? Well, let me give you a high  level overview of how I do it. Someday I want to make a more in-depth video about   my process and this will be just a short  overview to not make this video too long. Think of the PlayStation 1 as a  very limited computer from 1994. What makes PS1 very different from modern   PCs is that it loads the software from  CDs. It also has no operating system.

[**2:12**](https://youtu.be/dsA2sQ-rThU?t=132) When you normally compile your code on the PC,   the compiler creates an executable which  contains machine code native for your CPU, for example, x86-64 or ARM. The PS1 CPU is based on the MIPS R3000 processor,  which uses MIPS I instruction set architecture. And here's the cool thing... Modern GCC still supports  this processor architecture   and you can still compile PS1 games with it. On Linux, you need to install the  gcc-mipsel-linux-gnu package and

[**2:47**](https://youtu.be/dsA2sQ-rThU?t=167) that's all you need to compile  programs which can run on PS1. Back in the '90s, pretty much all  PS1 developers used an official   SDK called Psy-Q that was developed  by SN Systems Limited and Psygnosis. It was relatively high level and didn't  expose low-level PS1 hardware details much. The games were usually written  in C programming language. Psy-Q also had a library called libgs,  which was even higher level sort of   a game engine which made it even  easier to write games for the PS1.

[**3:22**](https://youtu.be/dsA2sQ-rThU?t=202) The official SDK also had many helper tools   and converters bundled with it to  make the development even easier. I don't use Psy-Q for my projects and I don't  recommend using it for making homebrew these days. Sony probably doesn't care about you  using it for non-commercial stuff but   might get mad if you release a commercial game,   because Psy-Q is their proprietary  code which was never publicly released. Psy-Q is closed source, so you can't inspect  functions to see how they really work,

[**3:55**](https://youtu.be/dsA2sQ-rThU?t=235) unless you reverse engineer them from assembly. Also, its API design is very outdated,   at times very confusing and overall  not very pleasant to work with. There are other ways of doing PS1  development which are much better. You can go "bare metal" and make your  own SDK pretty much from scratch. For my PS1 projects, I use SDK called  PSYQo or (pronounced as) "Psycho". It's a modern PS1 SDK written  in C++ by Nicolas Noble.

[**4:29**](https://youtu.be/dsA2sQ-rThU?t=269) It's mostly a low-level wrapper around  some of the hardware's basic functionality,   but it also contains a bunch of helpers to make  the process of the development less painful. Compared to Psy-Q, you are mostly on your own. You need to write all of your engine  and game code pretty much from scratch. Note that despite its name, PSYQo  is not related to Psy-Q at all. It's written from scratch and doesn't  use any of Sony's proprietary code. This is good, because it means  that games developed with PSYQo

[**5:01**](https://youtu.be/dsA2sQ-rThU?t=301) are pretty much in the clear from  a legal standpoint (I hope...). I've been using PSYQo for a long time  and collaborated with Nicolas closely   to improve it as I discovered many bugs  and limitations while I was using it. I believe that PSYQo is  incredibly good and stable now. The only major thing it  lacks is memory card support. Nicolas has also written a bunch  of handy tools for PS1 development. For example, with a CD Authoring  tool, you can bundle your assets

[**5:32**](https://youtu.be/dsA2sQ-rThU?t=332) and game executable into an ISO, which you  can then burn on a CD-R and run on your PS1. Note that you need to have a way to run  unlicensed software on your console to do it. You can do it via FreePSXBoot,  mod chip, or a cheat cartridge. You still can't make a CD-R, which would  trick the PS1 copy protection in 2026. But at least with methods like  FreePSXBoot, or using a cheat cartridge,   you can run homebrew without doing any  hardware modifications to your PS1.

[**6:04**](https://youtu.be/dsA2sQ-rThU?t=364) There's another way to run homebrew on  PS1. I have a second PlayStation 1 with   X-Station installed, so I don't need  to burn hundreds of CDs, thankfully. I can just put the ISO file on the  SD card and then run my game from it. I mostly run my game in DuckStation PS1  emulator while I develop it (my game), and   only occasionally run the game on the PS1 itself.

[**6:34**](https://youtu.be/dsA2sQ-rThU?t=394) DuckStation emulates PS1 extremely well,   so I rarely see discrepancies between  the emulator and the real hardware. But sometimes I do. And it's not very fun. Some of the bugs I have encountered  took me literal *days* to solve. Okay, what about the game assets  that you put inside the ISO? Well, it's complicated... PS1 can't work with any normal  image, audio, and model formats.

[**7:07**](https://youtu.be/dsA2sQ-rThU?t=427) For sound effects, you can't just use .wav files. The PS1 sound processor can only play back samples  compressed with a specific ADPCM algorithm. Thankfully, there's an ADPCM  encoder written by Nicolas,   which can convert your .wav files  to the format that PS1 understands. Next, the textures. No, you can't use  JPEGs or PNGs. The texture data which   you need to upload to PS1 VRAM needs to be  either 16-bit raw color or indexed color.

[**7:39**](https://youtu.be/dsA2sQ-rThU?t=459) I have written a custom tool that converts my PNG   textures to the TIM format  which is also used in Psy-Q. It's a pretty simple format which can store  all the formats that PS1 VRAM supports. Note that my texture converter can  also quantize textures to the needed   amount of colors and can downscale images as well. I use ImageMagick for quantization  and other image processing. Next, the models and levels. And you guessed  it... While you technically could write an OBJ

[**8:11**](https://youtu.be/dsA2sQ-rThU?t=491) or glTF parser for PS1, parsing models from these  formats would be extremely slow on the PS1 CPU. It's much more efficient to store models  and levels in custom binary format. I implemented a Blender exporter  which converts models and levels   directly from the .blend file  into my custom binary format. It also exports skeletal  animations stored in the files. I also created a tool which makes  texture atlases for each level   so that I don't need to manually  position all the textures in VRAM.

[**8:45**](https://youtu.be/dsA2sQ-rThU?t=525) I previously needed to do it and  it was extremely time consuming. Implementing all this took a lot of time  but the result is extremely easy to use. I can just modify the .blend file, run the  shell script which builds the assets and   put them in the ISO and instantly  see all the changes in the game. And now let's finally see how  I developed Yume Nikki PS...

### Yume Nikki PS Development Start

[**9:18**](https://youtu.be/dsA2sQ-rThU?t=558) Yume Nikki is an indie game first released in 2004  by a mysterious Japanese developer called Kikyama. In the game, you explore the dream  worlds of a girl named Madotsuki. In this world, you collect effects which change  your appearance and give you different abilities. With these abilities, you can interact with   various NPCs and get to new worlds  which you couldn't access before. I love Yume Nikki a lot.

[**9:50**](https://youtu.be/dsA2sQ-rThU?t=590) I like its atmosphere, its  aesthetics, its surreal worlds,   and all the little details  and care put in this game. I love how mysterious it is and how it breaks many  video game conventions and always surprises you. You never know what to expect from it. When I got the idea to make a re-imagining of Yume  Nikki for PS1, I set a simple goal for myself. Make Madotsuki's house and two dream worlds.

[**10:22**](https://youtu.be/dsA2sQ-rThU?t=622) The scope looked small enough and I thought  that I could do it in two or three weeks. After all, you just walk in the game  and collect a bunch of effects, right? Well... I was very off with my estimate. As you'll see, there's much,  much more that I had to do. I started by modeling Madotsuki's house. I used Yume Nikki's pixel art for the  textures, which looked pretty good on PS1. I initially wanted to redraw all the textures and  make them higher definition, but this was one of

[**10:56**](https://youtu.be/dsA2sQ-rThU?t=656) the things that I had to pass on to finish  the demo in a reasonable amount of time. When I started the project, I  didn't start coding everything   from scratch. I used my cat game code as a base. So, initially my cat character was  running around Madotsuki's house. The way I reduced the code was by putting  a lot of #ifdef statements into it. Basically, an #ifdef in C++ lets you choose the   portion of the code which gets  compiled into your executable. So instead of copying thousands of lines of  code from a cat game and modifying the copy,

[**11:30**](https://youtu.be/dsA2sQ-rThU?t=690) I just inserted a bunch of #ifdefs and  reused big portions of my code base. Reusing my code that way was nice because  when I fixed the bugs in Yume Nikki PS and   added new features to my engine, all of that  was also applied to my main project as well. After I did the apartment, I started  working on Madotsuki's model. I was very anxious about getting her model just  right as I'm still not a very good modeler. Eventually, I just started  modeling a temporary model,

[**12:00**](https://youtu.be/dsA2sQ-rThU?t=720) but it started looking pretty nice after a while. As always, sometimes the best way  to get over your anxiety is to just   pretend that you are doing a temporary or  throwaway thing and just keep improving it. Don't try to make it perfect on the first try.   There's almost always time  to improve your work later. Madotsuki's 3D design was inspired  by these models by Colin Armistead. I was also inspired by the work  of an animator who goes by the

[**12:33**](https://youtu.be/dsA2sQ-rThU?t=753) name Gobou whose animations I absolutely adore. Once I did the first iteration of Madotsuki's  model, the project started looking nice. So I decided to announce it to the world. I had a few of my tweets going viral  before, so I expected to get some attention,   but I didn't expect to get so much hype when  I announced the project in its rough state. It wasn't just a small learn project anymore.

[**13:04**](https://youtu.be/dsA2sQ-rThU?t=784) Now, I had to do my best and make  something that people will find enjoyable,   and this is partly why the project  took much longer than I anticipated. A lot of the comments about the announcement  were very positive. But there were some   somewhat sarcastic comments which  struck me in particular though... These ones. The initial model had a mouth and so   Madotsuki kind of looked like  Frisk from Undertale, indeed. Realizing my mistake, I removed the mouth and

[**13:34**](https://youtu.be/dsA2sQ-rThU?t=814) redraw her eyes a bit differently. Once  I did that, the model became much cuter. Eventually, I did a lot of adjustments  to make the model even better. And I also rigged and animated it. I especially like how I managed to animate   Madotsuki's hair to make the  animations more interesting. I also modeled the balcony, which  looked pretty atmospheric with the fog.

[**14:10**](https://youtu.be/dsA2sQ-rThU?t=850) One goal I set for myself was to do everything  as close to the original as possible. I wanted to include every single thing that you   could do in the original game in the  areas which were included in the demo. I didn't want people to try to  do something and get disappointed   that the thing they were looking  for is not present in the demo. One of these things was NASU. It's a fun little game that you  can play in Madotsuki's apartment. The game is pretty simple. You can walk left  and right and jump to catch a fallen eggplant.

[**14:43**](https://youtu.be/dsA2sQ-rThU?t=883) Should be easy to implement in a few hours, right? Well, there are other things... There's a bonus eggplant that  sometimes appears and jumps around. There's a mechanic which gives you extra points  when you catch two eggplants at the same time. There's also a cheat code which makes  your head look like an eggplant and   spawns bonus eggplants with a  higher probability and so on. Programming and polishing this mini  game took two whole days in the end.

### Block World

[**15:21**](https://youtu.be/dsA2sQ-rThU?t=921) And then I started to work on The Nexus. The floor pattern was pretty interesting to model.   I couldn't just put a huge image in the  PS1 VRAM since it wouldn't fit there. So, I noticed that this pattern is pentagonal. And this has helped me save a lot of space  by only using a small texture for the floor.

[**16:00**](https://youtu.be/dsA2sQ-rThU?t=960) I also added lights on top of  doors which you can enter to   highlight which worlds are available in the demo. For the dream sequence, I need  to do a pixelization fade effect. The way it's done is somewhat tricky  because PS1 can't do fullscreen shaders. I basically copy the last drawn  frame into the upper right corner   of VRAM and then repeatedly scale  it down in another part of VRAM. Then I stretch the resulting pixelated  texture to the currently drawn framebuffer.

[**16:35**](https://youtu.be/dsA2sQ-rThU?t=995) The fade in effect is the same  thing but done in reverse. I also implemented the ability to sit  in the chair and ride it in the dream. I especially love how the cutscene where  you start riding the chair turned out.

[**17:06**](https://youtu.be/dsA2sQ-rThU?t=1026) The "iris" effect that appears when  you sit in the chair or enter the   dream world was also tricky to implement  since PS1 doesn't have a stencil buffer. So I just draw a bunch of triangles on top  to obscure the image and it works pretty   fine. I learned about this effect from  a wonderful PS1 game called Dr. Slump.

[**17:38**](https://youtu.be/dsA2sQ-rThU?t=1058) Now that I was done with Madotsuki's apartment,   it was time to start working  on the first dream world. The first real world I chose  to make was Block World. It's just a bunch of blocks.  Should be easy to do, right? Well, first of all, the level is HUGE.

[**18:08**](https://youtu.be/dsA2sQ-rThU?t=1088) It's also in the isometric perspective,  which meant that I couldn't just parse   the original map data and  create a 3D model from it. So, I had to count the block sizes and distances  between them by hand and model them one by one. I marked all the shapes I've done  on the map to not miss any of them. And it was also tricky to figure  out how the shapes were positioned   relative to each other. So, drawing a  bunch of lines in perspective helped. The modeling process took three  whole days and by the end of it,

[**18:42**](https://youtu.be/dsA2sQ-rThU?t=1122) the map looked like a madman  has scribbled all over it. The resulting level was tile perfect. Once I modeled the whole level,  I encountered a huge problem. The resulting mesh was so big  that I started running out of   memory on PS1 while trying to load the level. The PS1 RAM is very small, just 2 megabytes. Out of these two megabytes,

[**19:12**](https://youtu.be/dsA2sQ-rThU?t=1152) I only have around 1 megabyte free to  use for gameplay assets and objects. The other megabyte is occupied  by things like draw lists,   static game data, the compiled  game codes itself, and so on. The block world level mesh was 800 KB,  and I only had 400 KB of memory left. So, the game crashed once I tried to load it. So, I needed to compress  the level mesh a lot better. After some thinking, I came  up with a nice solution. Instead of storing the model spaces  in my custom 3D model format where

[**19:46**](https://youtu.be/dsA2sQ-rThU?t=1186) every quad face takes 36 bytes, I made a  much simpler format for the block meshes. For each face, I only store its single  point and assume that its size is 1x1 m. I also store the face color and its direction. Basically, the side of the cube which the  face represents. With this new format,   each face of the block mesh world occupies  only 10 bytes instead of 36 bytes. And so I have finally managed to bring block wall

[**20:17**](https://youtu.be/dsA2sQ-rThU?t=1217) size from 800 KB to 184 KB  and the crisis was averted. Everything fit into nicely and I even had quite  a lot of free memory space for other things. Now, I also realized that I could pre-calculate   the lighting by slightly changing each face's  base color depending on the side of the cube. If it was the top side, the color was  made lighter. If it was a face on the   sides or the bottom, the color became darker.

[**20:52**](https://youtu.be/dsA2sQ-rThU?t=1252) This fake lighting made the block  world level look a lot better,   and it was now much easier to navigate in. After that, I added the first effect, HatAndScarf. At this point, I also spent a lot of time  working on the status and effects menus.

[**21:24**](https://youtu.be/dsA2sQ-rThU?t=1284) In fact, the pixel perfect recreations  of Yume Nikki's and RPGMaker menus. Measuring all the pixel offsets and writing  the code for the UI took many hours. And speaking of menus, the instruction screens  took a lot of time to implement as well.   I even created a new screen which  shows the PS1 gamepad controls.

[**21:58**](https://youtu.be/dsA2sQ-rThU?t=1318) I have also implemented the first NPC. Mafurako which is just Madotsuki's model with  head and scarf but with Madotsuki herself hidden. I wrote a simple AI for her to make  her wander around and teleport you   to a predetermined set of the points on the map. I also needed to add level background music now. I initially wanted to remake it as  sequenced music, but I eventually   decided to simply record the tracks from  the original game and make them loop.

[**22:32**](https://youtu.be/dsA2sQ-rThU?t=1352) The PS1 sound processor (SPU) has only 512  KB of memory available for storing samples,   so I had to compress the music and make it mono. Most of the songs in the original  game are pretty short loops,   so it was perfect for making  the music files small. I didn't want to bother with  audio streaming from the CD   since it can be problematic on old PS1 CD drives. Another problem with audio CD streaming is  that if the music is streamed from the CD,

[**23:02**](https://youtu.be/dsA2sQ-rThU?t=1382) it can't loop perfectly because  the drive has to seek backwards   and sometimes it takes seconds  to arrive at the correct sector. But if the samples are stored in the  SPU RAM, they can be looped perfectly. The rest of the Block World was easy. I added the dudes that float beneath the floor. I added the toilet and the  cutscene when you enter it. I also modeled the gate to the White Desert. Sorry, Monoko and Monoe fans, you  can't go in there in the demo.

[**23:35**](https://youtu.be/dsA2sQ-rThU?t=1415) One thing that I also added at  this point was the warp behavior. In Yume Nikki, once you reach the end of the map,  you get teleported, as if the map was infinite. But first, we need to talk  about parallel universes... It works the same way in 3D, and  the distance fog prevents you from   noticing the moment when  the teleportation happens.

[**24:07**](https://youtu.be/dsA2sQ-rThU?t=1447) And so the level was done and it was time to  move on to the next level, the Snow World. The Snow World was very simple to  do compared to the Block World. Recreating the level was pretty easy. It's just a bunch of igloos and some trees. I actually added more trees and hills  to make the level more interesting.

### Snow World

[**24:53**](https://youtu.be/dsA2sQ-rThU?t=1493) The distances between the objects in  my 3D recreation were much bigger than   in the original because of the  more realistic scale I chose. So, I needed to add some new objects  to make the level less empty,   but it was still very easy  to get lost in the level. In the initial version of the  demo, it was even a bigger problem. The fog distance was way too small because of the  performance and amount of polygons I could draw. I did some clever tricks to optimize  the level drawing and managed to greatly

[**25:24**](https://youtu.be/dsA2sQ-rThU?t=1524) increase the view distance, which made  navigating the snow world much easier. However, as I saw on many streams  later, people still got lost a lot. They walked a few steps forward and then they kept   turning in one direction and  eventually did a 360° turn. Then they stumble upon something they recognized   and started doing the same thing again,  but sometimes in a different direction. This made me realize that it's kind of hard

[**25:54**](https://youtu.be/dsA2sQ-rThU?t=1554) to navigate such levels in 3D  without the map or a compass. It's much easier to navigate Yume Nikki's levels  in 2D since it's easier to keep a mental map   of the level and know the exact direction  you're going just by looking at the screen. By the way, the fog used in  Snow World and the balcony is   actually the exact same technique  that was used in Silent Hill 1. If you want to know how it works, check  out my video which goes in depth about

[**26:25**](https://youtu.be/dsA2sQ-rThU?t=1585) how this effect is achieved on PS1 and  how this effect pushes PS1 to its limits. To finish the level, I had to model the  three NPCs which you can meet there: Kamakurako, Snow Woman or Yuki-Onna and Toriningen. Toriningen and Yuki-Onna took  approximately 6 hours to model each.

[**26:55**](https://youtu.be/dsA2sQ-rThU?t=1615) I think the models turned out pretty good. One thing I'm particularly proud  of is how I did the hair alpha   trick to make Toriningen's hair more interesting.

[**27:27**](https://youtu.be/dsA2sQ-rThU?t=1647) She also looks at you if you get  close, which I think looks pretty neat. Kamakurako was much easier to  model as it was just a recolor   of Madotsuki's model with a different hairstyle. I also implemented a cool  transformation into a snowman. With all levels and models being done,  it was time to do some final polish.

### Final Polish

[**28:10**](https://youtu.be/dsA2sQ-rThU?t=1690) I added a little cinematic that  plays when you start playing NASU.

[**28:41**](https://youtu.be/dsA2sQ-rThU?t=1721) I also added cool glow effect to the TV by adding  a bunch of semi-transparent quads to the TV model. I programmed the Kalimba event which  sometimes happens when you turn   the TV on while dreaming. I also made  Madotsuki do a little dance during it.

[**29:17**](https://youtu.be/dsA2sQ-rThU?t=1757) I also made the bed bounce for fun. And I implemented the ability  to drop effects in the Nexus. I also added the Photo Mode, which  can be used to make cool screenshots. It has a "pose" function which makes Madotsuki  turn to the camera and strike the peace pose. And I just had to implement  Toriningen posing for the camera too.

[**29:49**](https://youtu.be/dsA2sQ-rThU?t=1789) Another fun thing that I did was changing the  PS1 boot logo. The PlayStation logo model is   not a part of the PS1 BIOS and it's actually  part of the license part of the ISO file. Check out this cool video by Jimmy Breck-McKye  to learn more about the PS1 boot process. So, by modifying this part of the ISO,   you can change the model and the  license text to whatever you want. A while ago, I did some reverse  engineering and figured out how to do it.

[**30:21**](https://youtu.be/dsA2sQ-rThU?t=1821) With this incredible power,  I could do things like this: So with Yume Nikki PS I thought,  wouldn't it be cool to add Uboa there? I also found out that it's  possible to insert hiragana   in the license text. So I replaced it with

[**30:52**](https://youtu.be/dsA2sQ-rThU?t=1852) AAAAAAAAAAAA the significance of which might  be familiar to Yume Nikki fans. Here's how the boot sequence  looked in the final version:

[**31:24**](https://youtu.be/dsA2sQ-rThU?t=1884) While I was developing the game, I had  a to-do list on my desk which helped   me stay focused and see how many things I  had to do before I could release the demo. Unfortunately, this list grew into another list. But still it kept me sane by seeing the scope was   somewhat defined and I was getting  closer and closer to the release. I also decided not to playtest  the game with other people. I was anxious that they wouldn't like some parts   of it and I would have to spend  many more weeks on the demo.

[**31:57**](https://youtu.be/dsA2sQ-rThU?t=1917) By not letting others playtest the  game before the public release,   I was putting myself at risk by  having the game riddled with bugs. But thankfully, it all worked out  and it was pretty stable overall. When I was 100% done with the content, I spent  a few days dealing with some of the worst bugs. And then I spent a few hours  creating a pretty cover for the game ...and actually printed it.

[**32:34**](https://youtu.be/dsA2sQ-rThU?t=1954) With everything being done, I spent a  few hours creating an itch.io page and   another few hours creating a trailer for the game and have finally hit post... I knew that the demo's release  wouldn't fly under the radar,

### The Reaction

[**33:05**](https://youtu.be/dsA2sQ-rThU?t=1985) since many of my tweets about it went viral. People were extremely excited for the demo. And during the week before the demo's release,   my tweets got 1 million views  in a week, which is just insane. But I still didn't know how people  would react to the game itself. I knew that it looks pretty good, and  the PS1 aesthetic added free vibe points,   but I didn't know if the game would  be as satisfying as the original. I wasn't sure if fans would find my work good

[**33:35**](https://youtu.be/dsA2sQ-rThU?t=2015) enough since with the game as  niche and cult as Yume Nikki, you'd expect to find a lot of  very dedicated and opinionated   fans who might have found my reimagining  lacking, unsatisfying or even offensive. However, I started getting a lot  of incredibly positive comments, and just minutes after the release,  people were already playing my game   on Twitch and releasing YouTube videos about it, and they all liked it a lot.

[**34:15**](https://youtu.be/dsA2sQ-rThU?t=2055) It was my first experience of seeing  someone play something I made on game   and it was also my first experience  interacting with streamers as well.

[**34:50**](https://youtu.be/dsA2sQ-rThU?t=2090) I didn't want the streamers to feel  pressured by announcing myself in   the chat if I caught the stream as  they were just booting the game. So, I didn't announce my arrival until  they had some time to express how they   felt about it and whether they liked it or not. And then I posted, "Hey, I  made this game" in the chat. I didn't expect streamers to  react with such amazement and joy. And then now it's like, "Oh,   you're the person who was streaming TF2  and forgot they had a strange model." Oh... NOOO, Elias, I can't be talking about

[**35:23**](https://youtu.be/dsA2sQ-rThU?t=2123) this when you come in. Don't worry  about Pyro's TF2 inflation models. It's not important. Hi, Dev. "Thank you for playing  the demo". Dev, fucking good. This is so good. This is so  ridiculously good. It's crazy. It's fucking crazy. I cannot believe you  pulled this off in one and a half months. This is fucking God Tier. This is ridiculous... It was cool seeing people run  Yume Nikki PS on many things,   not just on PlayStation 1, but on PS2, PS  Vita, and even on mobile phones and iPads.

[**35:59**](https://youtu.be/dsA2sQ-rThU?t=2159) It's interesting to think that a PS1 game is  essentially more portable than any PC game. Even when many years pass, my PS1 games will  still run on PS1 emulators on future platforms. I didn't expect to see many  people to love something I made. It seemed like the more enthusiastic the person  playing was about, the more they loved the demo. And then something cool happened. I saw that Vinny Vinesauce had  started a stream with a curious title.

[**36:33**](https://youtu.be/dsA2sQ-rThU?t=2193) I didn't know if he would play  my game on the stream or not,   but with my game going viral on Twitter and the 2D   to 3D thing matching exactly what I did,  it felt like my game could be featured. I became extremely nervous because just a  couple of hours before Vinny's stream started,   another person streamed my game and found a few   game breaking bugs caused by  my latest changes to the game. Seeing my game crashing on Vinny's  stream while thousands of people   were watching would have been pretty devastating.

[**37:05**](https://youtu.be/dsA2sQ-rThU?t=2225) It was 4:00 a.m. I was exhausted from the  work and the segment still didn't start. So, I went to sleep, but I kept randomly waking up  and checking out what was going on on the stream. Finally, the segment ended and I saw  that my game didn't make it after all. The next day, I went to the unofficial  VOD channel and scrolled through it. There were many cool games  that Vinny played that day,   but it seemed like he didn't  play Yume Nikki after all. I felt kind of stupid about  panicking so much about nothing.

[**37:37**](https://youtu.be/dsA2sQ-rThU?t=2257) But then someone messaged me and said  that Vinny has played my game, in fact. I rushed back to the VOD  and have finally found it. I couldn't believe it. I was a big fan of Vinny's streams for years and  having him play something I made was incredible. And what's more, here's what he thought about it: This is cool.

[**38:07**](https://youtu.be/dsA2sQ-rThU?t=2287) This is really cool, actually, At this point, I knew I had finally  made it as a game developer. The game didn't crash during the stream, but  somehow Vinnie found the debug menu by accident. And from the dozens of streams I  watched, he was the only one to do it. I It's L1-Square-Triangle, by the way. A few days later, another legend and a favorite  streamer of mine, Joel, played the game as well.

[**38:40**](https://youtu.be/dsA2sQ-rThU?t=2320) And not just in one, but in two streams. All right. That's "Matsuoko". Oh, okay. Okay. Tilt like this and then we can... This is fucking cool. Okay. Weeee. Weeeee. Yeah. Yeah. That's what I'm talking about.  Woo! Okay. Wee. Okay. *laughs*

[**39:14**](https://youtu.be/dsA2sQ-rThU?t=2354) Seeing Joel enjoy the game and play it  for so long made me very proud of my work. In two weeks that followed since the demo  was released, I decided to port it to PC. I wanted the game to be more accessible to  people without having to mess with PS1 emulation. Plus, I wanted to make my code base future  proof because if I wanted to release my   PS1 games on Steam, I probably would be able  to just drop an ISO wrapped in an emulator. Porting to PC was a fun  experience and took about 3 weeks.

[**39:48**](https://youtu.be/dsA2sQ-rThU?t=2388) I basically made a mini PS1 emulator, but that's a   topic for another video since there are  many things that went into the process. Porting to PC also made it possible  for me to make a browser version. Now,   you didn't even need to download the game. You can just play it from your browser. Yume Nikki's fan base has accepted my  game to the extent I still can't believe. I have received incredible praise  on Itch, YouTube and on Twitter.

[**40:21**](https://youtu.be/dsA2sQ-rThU?t=2421) I'm incredibly thankful to the people who played  the game and wrote this wonderful comments. Thank you. A lot of people also found Yume Nikki through my   game which made me realize that Yume  Nikki was still a very niche game. I hope that my demo was a nice little  preview of what Yume Nikki is like and   the game got new fans when they  experienced the original version. A couple of wonderful people even did fan art  of Yume Nikki PS, which is just unbelievable.

[**41:05**](https://youtu.be/dsA2sQ-rThU?t=2465) Making this demo taught me a lot. I realized that you can fit a lot of polish and  complexity even into a small part of your game. All these little details I noticed made  me appreciate the original game even more   and motivated me to aim for the same  amount of polish in my own projects. After spending 3 months working on Yume  Nikki PS, I was kind of burned out. A small two week project spiraled into  something much bigger than I expected,   but it was so so worth it.

[**41:36**](https://youtu.be/dsA2sQ-rThU?t=2496) For now, I have returned to  working on my original game. Remember, this is what Yume Nikki  PS was initially meant to be,   a small demo project for me to become a  better game developer and test my engine. I hope that my original game would  eventually evolve into something   cool and would bring as much joy  to people as Yume Nikki PS did. I'll try my best to make it a novel,  charming, and enjoyable experience. And someday, I might return to Yume Nikki PS once   again and add some new effects  and areas for people to explore.

[**42:17**](https://youtu.be/dsA2sQ-rThU?t=2537) In the meantime, don't forget to  check out the demo I released on   Itch and see if all this hard  work was worth it in the end. And please, play Yume Nikki  if you never did before. It's one of the greatest games ever created... Feel free to ask any questions in the comments and [SUBSCRIBE] to get random cool gameplay  videos of my PS1 game from time to time.

[**42:49**](https://youtu.be/dsA2sQ-rThU?t=2569) Or, if you want to follow my work more closely,   follow me on Twitter, since that's  where I post my day-to-day work. I hope that you found the video  interesting. Thank you for watching.
