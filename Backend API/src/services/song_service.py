from services.utils import checkValidParameterString
from database.Database import Database
from gridfs import GridFS
from fastapi import HTTPException
from fastapi.responses import Response
from model.Genre import Genre
from model.Song import Song
from services.artist_service import check_artists_exists, add_song_artist, delete_song_artist
from model.TokenData import TokenData
from sys import modules
from pymongo.errors import PyMongoError
import boto3
from botocore.exceptions import ClientError
import base64
import json
import io
import librosa

""" Insert songs with format [files,chunks] https://www.mongodb.com/docs/manual/core/gridfs/"""

if "pytest" in modules:

    gridFsSong = GridFS(Database().connection, collection='test.cancion')
    fileSongCollection = Database().connection["test.cancion.files"]
    songCollection = Database().connection["canciones.streaming"]


else:

    gridFsSong = GridFS(Database().connection, collection='cancion')
    fileSongCollection = Database().connection["cancion.files"]
    songCollection = Database().connection["canciones.streaming"]

s3 = boto3.resource('s3')
s3_client = boto3.client('s3')
song_bucket = s3.Bucket('canciones-spotify-electron')
bucket_base_path = "canciones/"
distribution_id = "E1QGP8NNLHDTBO"

def check_song_exists(name: str) -> bool:
    """ Check if the song exists or not

    Parameters
    ----------
        name (str): Song's name

    Raises
    -------

    Returns
    -------
        Boolean
    """

    return True if songCollection.find_one({'name': name}) else False



def check_jwt_user_is_song_artist(token: TokenData, artist: str) -> bool:
    """ Check if the user is the song artist

    Parameters
    ----------
        token (TokenData): token with the user data
        artist (str) : artist name

    Raises
    -------
        Unauthorized 401

    Returns
    -------
        Boolean
    """

    if token.username == artist:
        return True
    else:
        raise HTTPException(
            status_code=401, detail="El usuario no es el creador de la canción")


def get_cloudfront_url(resource_path):
    cloudfront_client = boto3.client('cloudfront')

    # Get the CloudFront domain name associated with the distribution
    response = cloudfront_client.get_distribution(Id=distribution_id)
    domain_name = response['Distribution']['DomainName']

    # Construct the CloudFront URL
    cloudfront_url = f"https://{domain_name}/{resource_path}"

    return cloudfront_url


def get_song(name: str) -> Song:
    """ Returns a Song file with attributes and streaming url "

    Parameters
    ----------
        name (str): Song's name

    Raises
    -------
        400 : Bad Request
        404 : Song not found

    Returns
    -------
        Song object
    """

    if name is None or name == "":
        raise HTTPException(
            status_code=400, detail="El nombre de la canción es vacío")

    song = songCollection.find_one({'name': name})
    if song is None or not check_song_exists(name=name):
        raise HTTPException(
            status_code=404, detail="La canción con ese nombre no existe")

    """ song_bytes = song_bytes.read()
    # b'ZGF0YSB0byBiZSBlbmNvZGVk'
    encoded_bytes = str(base64.b64encode(song_bytes))

    song_metadata = fileSongCollection.find_one({'name': name}) """

    cloudfront_url = get_cloudfront_url(f"{bucket_base_path}{name}.mp3")


    song = Song(name, song["artist"], song["photo"], song["duration"], Genre(
        song["genre"]).name, cloudfront_url , song["number_of_plays"])

    return song


def get_songs(names: list) -> list:
    """ Returns a list of Songs that match "names" list of names  "

    Parameters
    ----------
        names (list): List of song Names

    Raises
    -------
            400 : Bad Request
            404 : Song not found

    Returns
    -------
        List<Song>

    """

    songs: list = []

    for song_name in names:

        songs.append(get_song(song_name))

    return songs


def get_all_songs() -> list:
    """ Returns a list of all Songs file"

    Parameters
    ----------

    Raises
    -------

    Returns
    -------
        List <Song>

    """

    songs: list = []

    songsFiles = songCollection.find()

    for songFile in songsFiles:

        songs.append(get_song(songFile["name"]))

    return songs


async def create_song(name: str, genre: Genre, photo: str, file, token : TokenData) -> None:
    """ Returns a Song file with attributes and a song encoded in base64 "

    Parameters
    ----------
        name (str): Song's name
        genre (Genre): Genre of the song
        photo (str) : Url of the song thumbnail
        file (FileUpload): Mp3 file of the song
        token (TokenData) : jwt token decoded


    Raises
    -------
        400 : Bad Request
        401 : Invalid credentials
        404 : Artist Not Found / Song not found

    Returns
    -------
    """

    artist = token.username

    if not checkValidParameterString(name) or not checkValidParameterString(photo) or not checkValidParameterString(artist) or not Genre.checkValidGenre(genre.value):
        raise HTTPException(
            status_code=400, detail="Parámetros no válidos o vacíos")

    check_artists_exists(artist_name=artist)

    if check_song_exists(name=name):
        raise HTTPException(status_code=400, detail="La canción ya existe")

    if not check_artists_exists(artist_name=artist):
        raise HTTPException(status_code=404, detail="El artista no existe")


    try:
        # Assuming 'audio_bytes' contains the audio data in bytes
        audio_data, sample_rate = librosa.load(io.BytesIO(file), sr=None)

        # Calculate the duration in seconds
        duration = librosa.get_duration(y=audio_data, sr=sample_rate)

    #! If its not a sound file
    except:
        duration = 0

    try:
        s3_client.put_object(Body=file, Bucket=song_bucket.name, Key=f"{bucket_base_path}{name}.mp3")

        file_id = songCollection.insert_one({
            'name':name, 'artist':artist, 'duration':duration, 'genre':str(genre.value), 'photo':photo, 'number_of_plays':0})
        add_song_artist(artist, name)

    except PyMongoError as e:
        raise HTTPException(status_code=500, detail="Error interno del servidor al interactuar con MongoDB")

    except ClientError  as e:
        print(e)
        raise HTTPException(status_code=500, detail="Error interno del servidor al interactuar con AWS")

    except Exception as e:
        raise HTTPException(status_code=500, detail="No se pudo subir la canción")



def delete_song(name: str) -> None:
    """ Delete the song with his asociated chunk files "

    Parameters
    ----------
        name (str): Song's name

    Raises
    -------
        400 : Bad Parameters
        404 : Artist not found

    Returns
    -------
    """

    if not checkValidParameterString(name):
        raise HTTPException(
            status_code=400, detail="El nombre de la canción no es válido")

    result = songCollection.find_one({'name': name})

    if not result or not result["_id"]:

        raise HTTPException(status_code=404, detail="La canción no existe")

    try:
        songCollection.delete_one({'name': name})
        s3_client.delete_object(Bucket=song_bucket.name, Key=f"{bucket_base_path}{name}.mp3")
        delete_song_artist(result["artist"], name)

    except PyMongoError as e:
        raise HTTPException(status_code=500, detail="Error interno del servidor al interactuar con MongoDB")

    except ClientError  as e:
        print(e)
        raise HTTPException(status_code=500, detail="Error interno del servidor al interactuar con AWS")

    except Exception as e:
        raise HTTPException(status_code=500, detail="No se pudo subir la canción")






def update_song(name: str, nuevo_nombre: str, photo: str, genre: Genre,token : TokenData) -> None:
    """ Updates a song with name, url of thumbnail, duration, genre and number of plays, if empty parameter is not being updated "

    Parameters
    ----------
        name (str): Song's name
        nuevo_nombre (str) : New Song's name, if empty name is not being updated
        photo (str): Url of Song thumbnail
        genre (Genre): Genre of the Song
        number_of_plays (int): Number of plays of the Song
        token (TokenData) : token data of the user

    Raises
    -------
        400 : Bad Request
        401 : Unauthorized
        404 : Song Not Found

    Returns
    -------
    """


    if not checkValidParameterString(name):
        raise HTTPException(status_code=400, detail="Parámetros no válidos")

    result_song_exists: Song = get_song(name=name)

    if not result_song_exists:
        raise HTTPException(status_code=404, detail="La cancion no existe")

    check_jwt_user_is_song_artist(token,result_song_exists.artist)

    if checkValidParameterString(nuevo_nombre):
        new_name = nuevo_nombre
        fileSongCollection.update_one({'name': name}, {
            "$set": {'name': new_name, 'artist': result_song_exists.artist, 'photo': photo if photo and 'http' in photo else result_song_exists.photo, 'genre': Genre(genre).value if genre != None else Genre[result_song_exists.genre].value}})
    else:
        fileSongCollection.update_one({'name': name}, {
            "$set": {'name': name, 'artist': result_song_exists.artist, 'photo': photo if photo and 'http' in photo else result_song_exists.photo, 'genre': Genre(genre).value if genre != None else Genre[result_song_exists.genre].value}})


def increase_number_plays(name: str) -> None:
    """ Increase the number of plays of a song

    Parameters
    ----------
        name (str): Song's name

    Raises
    -------
        400 : Bad Request
        404 : Song Not Found

    Returns
    -------
    """

    if not checkValidParameterString(name):
        raise HTTPException(status_code=400, detail="Parámetros no válidos")

    result_song_exists: Song = get_song(name=name)

    if not result_song_exists:
        raise HTTPException(status_code=404, detail="La cancion no existe")

    songCollection.update_one({'name': name}, {
        "$set": {'number_of_plays': result_song_exists.number_of_plays+1}})
