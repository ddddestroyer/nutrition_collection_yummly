import os
import time
import re
import requests
from logging import getLogger, INFO, DEBUG, FileHandler, Formatter, StreamHandler
import numpy as np
import pandas as pd
import sys
import json
import traceback
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import NoSuchElementException, WebDriverException
from selenium.common.exceptions import TimeoutException
from pyvirtualdisplay import Display


BASE_URL = "https://www.yummly.com/recipes"
PROJECT_ROOT = os.path.expanduser("~/nutrition_collection_yummly")

class YummlyScraper:

    def __init__(self, logger):
        self.logger = logger

    def extract_category(self, category_list_url):

        # Webdriverを用いてそれぞれのカテゴリの文字列を取得する
        time.sleep(1)
        # driver_path = os.path.expanduser('~/chromedriver')

        display = Display(visible=0, size=(1024, 768))
        display.start()

        driver = webdriver.Chrome()
        driver.set_window_size(1024, 768)
        driver.get(category_list_url)

        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CLASS_NAME, 'toggle-filters')))
        filter_elm = driver.find_element_by_class_name('toggle-filters')
        filter_elm.click()
        time.sleep(1)

        recipe_list_page_html = driver.page_source
        recipe_list_page_soup = BeautifulSoup(recipe_list_page_html, 'lxml')
        category_div = recipe_list_page_soup.find('div', class_=re.compile("filter-group cuisines"))
        category_label = category_div.find_all("h3", class_="filter-item-title")

        driver.close()

        return [[i + 1, category.text] for i, category in enumerate(category_label)]

    # カテゴリの取得
    def scrape_category(self):

        category_list = self.extract_category(f"{BASE_URL}")

        category_columns = ["id", "name"]
        category_master_df = pd.DataFrame(category_list, columns=category_columns)
        category_master_df.to_csv(f"{PROJECT_ROOT}/data/category_master.csv",
                                  index=False, encoding='utf-8')

        # それぞれのカテゴリの文字をURLのパラメータ用に変換しておく
        category_string_list = []
        for num, category_text in enumerate(category_list):
            small_string = category_text[1].lower()

            if "barbecue" in small_string:
                small_string = small_string + "-bbq"
            if "&" in small_string:
                string = small_string.split(" ")
                category_string_list.append([num + 1, string[0]])
            else:
                category_string_list.append([num + 1, small_string])

        category_df = pd.DataFrame(category_string_list, columns=category_columns)

        return category_df

    # 料理情報の取得
    def scrape_cooking_info(self, recipe_page_item, cooking_id):
        time.sleep(0.5)
        cooking_info = {}
        cooking_info["cooking_id"] = cooking_id
        cooking_info["cooking_name"] = recipe_page_item["name"]
        recipe_image_url = recipe_page_item["image"]
        image_name = f"id_{cooking_id}.png"
        with open(f"{PROJECT_ROOT}/data/recipe_images/{image_name}", "wb") as f:
            f.write(requests.get(recipe_image_url).content)
        cooking_info["description"] = recipe_page_item["description"]
        cooking_info["for_how_many_people"] = recipe_page_item["recipeYield"]

        return cooking_info

    # 材料の取得
    def scrape_ingredients(self, recipe_page_item):
        ingredients_list = []
        for ingredients_item in recipe_page_item["recipeIngredient"]:
            ingredients = {}
            ingredients["ingredients"] = ingredients_item
            ingredients_list.append(ingredients)

        return ingredients_list

    # 栄養の取得
    def scrape_nutrition(self, recipe_page_item):
        nutrition_list = []
        try:
            nutrition_js = recipe_page_item["nutrition"]

            nutrition_keys = nutrition_js.keys()
            nutrition_values = nutrition_js.values()

            for key, value in zip(nutrition_keys, nutrition_values):
                nuttirion = {}
                if value == "NutritionInformation":
                    continue

                # Contentという文字列を取り除く
                nuttirion["nutrition_name"] = re.sub("Content", "", key)
                nuttirion["quantity"] = value
                nutrition_list.append(nuttirion)

        # 栄養素がない場合は空にする
        except KeyError:
            nutrition_empty = {}
            nutrition_empty["nutrition_name"] = ""
            nutrition_empty["quantity"] = ""
            nutrition_list.append(nutrition_empty)

        return nutrition_list

    def save_recipe(self, recipe_page_item, cooking_id, category_dict={}):

        # 料理情報
        cooking_info = self.scrape_cooking_info(recipe_page_item, cooking_id)
        df_cooking_info = pd.DataFrame(columns=list(cooking_info.keys()) + list(category_dict.keys()))
        df_cooking_info.loc[0] = list(cooking_info.values()) + list(category_dict.values())
        df_cooking_info.to_csv(f"{PROJECT_ROOT}/data/cooking_info.csv", index=False,
                               encoding="utf-8", header=False,
                               mode="a")

        # 材料
        ingredients_list = self.scrape_ingredients(recipe_page_item)
        df_ingredients = pd.DataFrame(columns=["ingredients"])
        for ingredient in ingredients_list:
            df_ingredients = df_ingredients.append(pd.Series(list(ingredient.values()), index=df_ingredients.columns),
                                                   ignore_index=True)
        df_ingredients["cooking_id"] = cooking_id
        df_ingredients.to_csv(f"{PROJECT_ROOT}/data/ingredients.csv", index=False,
                              encoding="utf-8", header=False, mode="a")

        # 栄養
        nutrition_list = self.scrape_nutrition(recipe_page_item)
        df_nutrition = pd.DataFrame(columns=["nutrition_name", "quantity"])
        for nutrition in nutrition_list:
            df_nutrition = df_nutrition.append(pd.Series(list(nutrition.values()), index=df_nutrition.columns),
                                               ignore_index=True)
        df_nutrition['cooking_id'] = cooking_id
        df_nutrition.to_csv(f"{PROJECT_ROOT}/data/nutrition.csv", index=False,
                            encoding="utf-8", header=False, mode="a")

    def scrape(self):

        root_category_df = self.scrape_category()

        for num, category_row in root_category_df.iterrows():

            while True:
                try:
                    # maxResultで取得したいコンテンツの数を調整する
                    recipe_html = requests.get(
                        f"{BASE_URL}?allowedCuisine=cuisine%5Ecuisine-{category_row['name']}&maxResult=500").content
                    recipe_soup = BeautifulSoup(recipe_html, "lxml")
                    recipe_contents = recipe_soup.find('div', class_='structured-data-info')
                    recipe_contents_js = recipe_contents.script.text
                    if recipe_soup.find('div', class_='structured-data-info'):
                        break
                except AttributeError:
                    print("wait 3 seconds")
                    time.sleep(3)

            recipe_json = json.loads(recipe_contents_js)
            recipe_items = recipe_json['itemListElement']

            for order_in_item, recipe_item in enumerate(recipe_items):
                order_in_item += 1
                cooking_num = str(int(category_row["id"])*10000 + order_in_item)
                cooking_id = cooking_num.zfill(6)

                category_dict = {"root_id": category_row["id"]}

                self.save_recipe(recipe_item, cooking_id, category_dict)


if __name__ == "__main__":

    logger = getLogger()
    logger.setLevel(DEBUG)
    formatter = Formatter(fmt='%(asctime)-15s: %(pathname)s:l-%(lineno)d:\n\t[%(levelname)s] %(message)s')

    stream_info_handler = StreamHandler(stream=sys.stdout)
    stream_info_handler.setLevel(DEBUG)
    stream_info_handler.setFormatter(fmt=formatter)
    logger.addHandler(stream_info_handler)

    yummly_scraper = YummlyScraper(logger)
    yummly_scraper.scrape()