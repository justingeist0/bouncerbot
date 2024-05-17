import os

import discord
from discord.ext import commands
from PIL import Image
import requests
from io import BytesIO
import imagehash
import random
from pymongo import MongoClient
from difflib import SequenceMatcher

uri = os.getenv("DB")

intents = discord.Intents.all()
intents.members = True  # Enable member updates.
bot = commands.Bot(command_prefix='!', intents=intents)

client = MongoClient(uri)
db = client['serverlessinstance0']
server_collection = db['bouncer']
scammer_collection = db['bouncer-scammers']
cache = {}


@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user.name}')


@bot.event
async def on_member_join(member):
    print(f"{member.name} {member.avatar} member joined {member.guild.name}")
    await kick_if_scammer(member.guild.id, member.id)


@bot.event
async def on_member_update(before, after):
    if after.bot:
        return
    await kick_if_scammer(after.guild.id, after.id)


@bot.command(name="bounceradd")
async def bouncer_add(ctx, target: str = None):
    if await ignore_user(ctx):
        return

    mentions = ctx.message.mentions
    if target and len(mentions) > 0:
        user = ctx.message.mentions[0]
    else:
        await ctx.message.reply(f"You must @ a member. If you want you can type @ yourself \"<@!{ctx.message.author.id}>\".")
        return

    user_id = user.id
    guild_id = ctx.guild.id

    if exists(guild_id, user_id):
        await ctx.message.reply(f"Impersonators already being blocked for <@!{user_id}>.")
    else:
        add_user_guild_entry(guild_id, user_id)
        await ctx.send(f"Now blocking impersonators of <@!{user_id}>.")


@bot.command(name="bouncerremove")
async def bouncer_remove(ctx, target: str = None):
    if await ignore_user(ctx):
        return
    if target and len(ctx.message.mentions) > 0:
        user = ctx.message.mentions[0]
    else:
        user = ctx.message.author

    user_id = user.id
    guild_id = ctx.guild.id

    if not exists(guild_id, user_id):
        await ctx.message.reply(f"<@!{user_id}> is not currently in my list.")
    else:
        delete_user_guild_entry(guild_id, user_id)
        await ctx.send(f"Removed <@!{user_id}> from my list.")


@bot.command(name="bouncerlist")
async def list_blockers(ctx):
    if await ignore_user(ctx):
        return
    user_ids = get_user_ids_by_guild_id(ctx.guild.id)
    user_ids = [f"<@!{i}>" for i in user_ids]
    message = "Here's the list of people who can't be impersonated:"
    for user in user_ids:
        message += f'\n- {user}'
    if len(user_ids) == 0:
        message += '\nEmpty. Type !bounceradd @user to add.'
    await ctx.message.reply(message)


@bot.command(name="bouncertestchannel")
async def list_blockers(ctx):
    if await ignore_user(ctx):
        return
    await on_user_kicked(ctx.message.author, ctx.message.author, ctx.guild.id, 0.0)


@bot.command(name="bouncercheck")
async def scan_server(ctx):
    if await ignore_user(ctx):
        return
    await ctx.message.reply("Starting scan... This may take a while, I'll update you when I'm done.")

    try:
        guild = await bot.fetch_guild(ctx.guild.id)

        # Fetch all members
        async for member in guild.fetch_members(limit=None):
            # await ctx.message.reply(f"looking at <@!{member.id}>")
            if not member.bot:
                await kick_if_scammer(guild.id, member.id)

        await ctx.message.reply("Scanning complete.")
    except Exception as e:
        print(f"error scanning {e}")
        ctx.message.reply(f"Error scanning probably got rate limited by discord. {e}")


@bot.command(name="bouncer")
async def bouncer_list_commands(ctx):
    if await ignore_user(ctx):
        return
    await ctx.message.reply("Okay boss, since you're an admin, here are my commands:\n\n"
                   "!bounceradd @NAME - Prevent non-admin members from sharing this user's profile picture. \n"
                   "!bouncerremove @NAME - Re-allow members to share this user's profile picture. \n\n"
                   "!bouncerlist - List the current users members can't share a profile picture with. \n\n"
                   "!bouncerposthere - Post the updates of the impersonators I kick in the channel this is typed in.\n\n"
                   "!bouncercheck - I'll \"check everyone's IDs\" and kick the people with fake ones. \n\n"
                   "I'm a bot, if you haven't yet click my name and join the server for further help preventing scams.")


@bot.command(name="bouncerwhoami")
async def justins_command(ctx):
    if ctx.message.author.id == 1007458938276556870:
        await ctx.message.reply("Hello my creator and real owner of https://fantasma.dev as I can see by your user id,\n\n He is here to help this server prevent scams and is the real deal.")
        return
    await ctx.message.reply("You are not my creator. This command is only for him. Move along.")


@bot.command(name="bouncerposthere")
async def bouncer_post_here(ctx):
    if await ignore_user(ctx):
        return
    # Retrieve the channel ID from the context
    channel_id = ctx.channel.id

    # Update or set the channel_id in the database
    guild_id = ctx.guild.id  # Assuming you are using guild IDs to differentiate between servers

    get_guild_object(guild_id)

    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True  # This creates a new document if one doesn't exist
    )

    delete_guild_from_cache(guild_id)

    # Inform the user
    if result.modified_count > 0 or result.upserted_id is not None:
        await ctx.message.reply("This channel has been set for bouncer posts.")
    else:
        await ctx.send("No update was needed, this channel was already set.")


async def ignore_user(ctx):
    if not ctx.guild:
        return True
    is_admin = any(role.permissions.administrator for role in ctx.author.roles)
    if not is_admin:
        await ctx.message.reply("I only listen to assigned admins. Move along.")
    return not is_admin


async def kick_if_scammer(guild_id, user_id):
    users = get_user_ids_by_guild_id(guild_id)
    if user_id in users:
        return

    guild = bot.get_guild(guild_id)
    if guild is None:
        guild = await bot.fetch_guild(guild_id)

    user = await guild.fetch_member(user_id)
    if user.bot:
        print('bot found, ignoring ', user.name)
        return

    discord_user = bot.get_user(user_id)
    if discord_user is None:
        discord_user = await bot.fetch_user(user_id)

    max_similarity = 0
    max_admin = None

    for u in users:
        admin_user = guild.get_member(u)
        if admin_user is None:
            admin_user = await guild.fetch_member(u)

        admin_avatar = admin_user.avatar
        user_avatar = user.avatar

        if check_if_names_match(admin_user, user):
            max_similarity = 1.0
            max_admin = admin_user
            break

        similarity = image_similarity(admin_avatar, user_avatar)

        if discord_user.avatar != user_avatar:
            test = image_similarity(admin_avatar, discord_user.avatar)
            if test > similarity:
                similarity = test

        if similarity > max_similarity:
            max_similarity = similarity
            max_admin = admin_user

        if similarity >= 1.0:
            break

    if max_similarity <= .8:
        return

    print("found someone fishy", user.id, user.name, " impersonating:", max_admin.name, max_similarity)
    try:
        if max_similarity >= .97:
            await user.ban(reason=f"{convert_to_percentage(max_similarity)} sure impersonating an Admin and most likely trying to scam people by messaging them posing as an admin.")
            save_scammer(user)
    except Exception as e:
        await on_user_kicked(user, max_admin, guild_id, max_similarity, e)
        return
    await on_user_kicked(user, max_admin, guild_id, max_similarity)


def check_if_names_match(admin, user):
    display = str(admin.display_name).lower().replace(" ", "")
    match_tests = [str(user.display_name).lower().replace(" ", ""), str(user.name).lower().replace(" ", ""), str(user.global_name).lower().replace(" ", "")]
    if display in match_tests:
        print("matched names ", str(match_tests), str(admin.display_name))
        return True
    return False


async def on_user_kicked(user, admin_user, guild_id, similarity, error=None):
    guild = get_guild_object(guild_id)
    if await needs_message_channel(guild):
        await admin_user.send(f"It's the bouncer. I don't have a channel to post someone was maybe impersonating you <@!{user.id}>. Pick a channel and type \"!bouncerposthere\" so I can post updates there.")
    else:
        if similarity >= 0.97:
            await send_guild_message(guild, get_kick_message(user.id))
        if error:
            await send_guild_message(guild,
                f"{convert_to_percentage(similarity)} similar profile to <@!{admin_user.id}>. Hey I tried to ban or kick this guy but don't have the permissions to. {error}")
        elif similarity >= 0.97:
            await send_guild_message(guild,
                f"Banned <@!{user.id}>. {convert_to_percentage(similarity)} similar profile to <@!{admin_user.id}>.")
        else:
            await send_guild_message(guild,
               f"@everyone <@!{user.id}> has a {convert_to_percentage(similarity)} similar profile picture to <@!{admin_user.id}>. Decide if you want to ban that member for impersonating.")
    try:
        await bot.get_channel(1240301259223863408).send(get_kick_message(user.id) + f" (from id: {guild_id})")
    except Exception as e:
        print(f"Can't post update to fantasma.dev discord server: {e}")


def convert_to_percentage(number):
    percentage = number * 100
    return f"{percentage:.0f}%"


def save_scammer(user):
    scammer_document = {
        "_id": user.id,  # Set the document ID to the user's ID
        "name": user.name,
        "discriminator": user.discriminator,  # This is typically the #1234 part of a Discord username
        "avatar_url": user.avatar.url  # If you want to store the user's avatar URL
    }
    try:
        scammer_collection.insert_one(scammer_document)
        print("User saved successfully.")
    except Exception as e:
        print(f"An error occurred: {e}")


async def needs_message_channel(guild):
    updates_channel = guild['channel_id']
    if updates_channel is None:
        return True
    channel = await bot.fetch_channel(updates_channel)
    if not isinstance(channel, discord.TextChannel):
        return True
    return False


async def send_guild_message(guild, message):
    updates_channel = guild['channel_id']
    if updates_channel is None:
        return
    channel = bot.get_channel(updates_channel)
    if not isinstance(channel, discord.TextChannel):
        return
    await channel.send(message)


def get_guild_object(guild_id):
    # Check if the guild_id is already in the cache
    if guild_id in cache:
        return cache[guild_id]

    guild_info = bot.get_guild(guild_id)
    print(f"Guild not in cache: {guild_info.name} {guild_info.id}")

    # If not in cache, fetch from MongoDB
    guild_document = server_collection.find_one({"guild_id": guild_id})

    if guild_document:
        print("Guild found in database, adding to cache.")
        # Add to cache
        cache[guild_id] = guild_document
        return guild_document
    else:
        new_server = { 'guild_id': guild_id, 'users': [], 'whitelist': [], 'is_premium': False, 'expiration': None, 'channel_id': None, 'name': guild_info.name }
        server_collection.insert_one(new_server)
        print("No guild found with the specified ID. Created new one.")
        return new_server


def delete_guild_from_cache(guild_id):
    # Remove the guild object from the cache if it exists
    if guild_id in cache:
        print("Guild removed from cache.")
        del cache[guild_id]
    else:
        print("Guild ID not found in cache.")


def get_kick_message(user_id):
    # Define the parts of the message
    prefixes = [
        f"Attention everyone! ",
        f"Just a heads-up! ",
        f"Alert! ",
        f"Guess what? ",
        f"News flash! ",
        f"BOOM! ",
        f"BAM! ",
    ]
    main_messages = [
        f"<@!{user_id}> has been kicked for scamming.",
        f"<@!{user_id}> tried to be a sneaky scammer, but no chance!",
        f"<@!{user_id}> was impersonating the top Gs here. Not on my watch!",
        f"<@!{user_id}> thought they could scam members. Think again!",
        f"<@!{user_id}> got booted for being a scammer.",
        f"<@!{user_id}> is gone and was a dirty scummy scammer. May they not be remembered.",
    ]
    suffixes = [
        " Staying vigilant, folks.",
        " I'll keep an eye out for any more sneaky snakes.",
        " Keeping us safe, team.",
        " Good riddance, I say!",
        " Goodbye to this sewage dwelling gremlin.",
        " Adios sir sneaky sneakerson.",
        " That scammer doesn't shower.",
        " Bye bye you stinky smelly scammer.",
    ]

    # Select one part from each list at random
    prefix = random.choice(prefixes)
    main_message = random.choice(main_messages)
    suffix = random.choice(suffixes)

    # Combine the parts into a final message
    return f"{prefix}{main_message}{suffix}"


def get_user_ids_by_guild_id(guild_id):
    guild = get_guild_object(guild_id)
    if 'users' in guild:
        return guild['users']
    else:
        return []


def delete_user_guild_entry(guild_id, user_id):
    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$pull": {"users": user_id}}
    )
    if result.modified_count > 0:
        delete_guild_from_cache(guild_id)


def exists(guild_id, user_id):
    users = get_user_ids_by_guild_id(guild_id)
    return user_id in users


def add_user_guild_entry(guild_id, user_id):
    result = server_collection.update_one(
        {"guild_id": guild_id},
        {"$addToSet": {"users": user_id}}
    )
    if result.modified_count > 0:
        delete_guild_from_cache(guild_id)


def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.5


image_cache = {}


def get_image(url):
    if url in image_cache:
        return image_cache[url]
    response = requests.get(url)
    image_cache[url] = response
    return response


def convert_to_smaller(url):
    url = str(url)
    return url


def image_similarity(url1, url2):
    try:
        # Download the images from the URLs

        response1 = get_image(convert_to_smaller(url1))
        response2 = requests.get(convert_to_smaller(url2))

        # Check if the responses are successful
        if response1.status_code != 200 or response2.status_code != 200:
            print("Failed to fetch image look into this.")
            return 0  # Return 0 for unsuccessful downloads

        # Open the downloaded images with PIL
        image1 = Image.open(BytesIO(response1.content))
        image2 = Image.open(BytesIO(response2.content))

        # Calculate the perceptual hash (pHash) of the images
        hash1 = imagehash.average_hash(image1)
        hash2 = imagehash.average_hash(image2)

        # Calculate the hamming distance between the hashes
        hamming_distance = hash1 - hash2

        # Calculate the similarity as a percentage
        similarity = 1.0 - (hamming_distance / 64.0)  # 64 is the max hamming distance for an average hash
        if similarity > .8:
            print("80% match", url1, url2)
        return similarity
    except Exception as e:
        print(f"Error: {str(e)}")
        return 0  # Return 0 in case of any errors


bot.run(os.getenv("DISCORD"))
