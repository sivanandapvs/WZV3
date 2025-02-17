from aioaria2 import Aria2WebsocketClient
from aioqbt.client import create_client
from aioaria2.exceptions import Aria2rpcException
from asyncio import gather
from pathlib import Path
from json import loads, JSONDecodeError

from .. import LOGGER, aria2_options


class TorrentManager:
    aria2 = None
    qbittorrent = None

    @classmethod
    async def initiate(cls):
        cls.aria2, cls.qbittorrent = await gather(
            Aria2WebsocketClient.new("http://localhost:6800/jsonrpc"),
            create_client("http://localhost:8090/api/v2/"),
        )

    @classmethod
    async def close_all(cls):
        await gather(cls.aria2.close(), cls.qbittorrent.close())

    @classmethod
    async def aria2_remove(cls, download):
        try:
            if download.get("status", "") in ["active", "paused", "waiting"]:
                await cls.aria2.forceRemove(download.get("gid", ""))
            else:
                await cls.aria2.removeDownloadResult(download.get("gid", ""))
        except Aria2rpcException as er:
            err_str = str(er)
            prefix = "unexpected result: "
            if err_str.startswith(prefix):
                err_str = err_str[len(prefix):]
            else:
                LOGGER.error(f"Aria2 Error: {err_str}")
                return
            try:
                data = loads(err_str)
                error_message = data.get("error", {}).get("message", "Unknown error")
                if not (error_message.startswith("GID ") and "is not found" in error_message):
                    LOGGER.error(f"Aria2 Exception: {error_message}")
            except JSONDecodeError:
                LOGGER.error(f"Aria2 Error: {err_str}")

    @classmethod
    async def remove_all(cls):
        await cls.pause_all()
        await gather(
            cls.qbittorrent.torrents.delete("all", True),
            cls.aria2.purgeDownloadResult(),
        )
        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)
        tasks = []
        tasks.extend(
            cls.aria2.forceRemove(download.get("gid")) for download in downloads
        )
        try:
            await gather(*tasks)
        except Exception:
            pass

    @classmethod
    async def overall_speed(cls):
        s1, s2 = await gather(
            cls.qbittorrent.transfer.info(), cls.aria2.getGlobalStat()
        )
        download_speed = s1.dl_info_speed + int(s2.get("downloadSpeed", "0"))
        upload_speed = s1.up_info_speed + int(s2.get("uploadSpeed", "0"))
        return download_speed, upload_speed

    @classmethod
    async def pause_all(cls):
        await gather(cls.aria2.forcePauseAll(), cls.qbittorrent.torrents.stop("all"))

    @classmethod
    async def change_aria2_option(cls, key, value):
        downloads = []
        results = await gather(cls.aria2.tellActive(), cls.aria2.tellWaiting(0, 1000))
        for res in results:
            downloads.extend(res)
            tasks = []
        for download in downloads:
            if download.get("status", "") != "complete":
                tasks.append(cls.aria2.changeOption(download.get("gid"), {key: value}))
        if tasks:
            try:
                await gather(*tasks)
            except Exception as e:
                LOGGER.error(e)
        if key not in ["checksum", "index-out", "out", "pause", "select-file"]:
            await cls.aria2.changeGlobalOption({key: value})
            aria2_options[key] = value


def aria2_name(download_info):
    if "bittorrent" in download_info and download_info["bittorrent"].get("info"):
        return download_info["bittorrent"]["info"]["name"]
    elif download_info.get("files"):
        if download_info["files"][0]["path"].startswith("[METADATA]"):
            return download_info["files"][0]["path"]
        file_path = download_info["files"][0]["path"]
        dir_path = download_info["dir"]
        if file_path.startswith(dir_path):
            return Path(file_path[len(dir_path) + 1 :]).parts[0]
        else:
            return ""
    else:
        return ""


def is_metadata(download_info):
    return any(
        f["path"].startswith("[METADATA]") for f in download_info.get("files", [])
    )
