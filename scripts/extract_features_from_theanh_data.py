#!/bin/env python3

"""
This script will extract environmental variables from The Anh's data,
which is WRF model's output. The list of variables to be extracted are:
    * Absolute vorticity
    * Relative humidity
    * Temperature
    * Geopotential height
    * Vertical Velocity
    * U-wind and V-wind
    * Cape
    * Surface Temperature
    * Surface Pressure

In addition, to be compatible with the original data (NCEP FNL reanalsysi),
the script will also extract the domain from 5 degree North to 45 degree North,
and 100 degree West to 260 degree East,
and vertical pressure levels are 19 mandatory levels
(1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200).
"""

import argparse
from collections import OrderedDict
import logging
from netCDF4 import Dataset, num2date
import numpy as np
import numpy.typing as npt
import os
import wrf
import xarray as xr

PRESSURE_LEVELS = [
    1000, 975, 950, 925, 900, 850, 800, 750, 700,
    650, 600, 550, 500, 450, 400, 350, 300, 250, 200,
]
TIME_FORMAT = '%Y%m%d_%H_%M'

def parse_arguments(args=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'inputfile',
        action='store',
        help='Path to the input file.',
    )
    parser.add_argument(
        'outputdir',
        action='store',
        help='Path to the output director.',
    )
    parser.add_argument(
        '--prefix', '-p',
        action='store',
        required=True,
        help='Prefix to be prepended to the output filename.'
    )

    return parser.parse_args(args)


def calculate_vorticity(ds: Dataset):
    return wrf.getvar(ds, 'avo')


def calculate_geopotential(ds: Dataset):
    ph = wrf.getvar(ds, 'PH')
    phb = wrf.getvar(ds, 'PHB')
    hgt = ph + phb
    return wrf.destagger(hgt, 0, meta=True)


def calculate_relative_humidity(ds: Dataset):
    return wrf.getvar(ds, 'rh')


def calculate_pressure(ds: Dataset):
    return wrf.getvar(ds, 'p', units='hPa')


def calculate_temperature(ds: Dataset):
    return wrf.getvar(ds, 'T')


def calculate_uwind(ds: Dataset):
    return wrf.getvar(ds, 'ua', units='m s-1')


def calculate_vwind(ds: Dataset):
    return wrf.getvar(ds, 'va', units='m s-1')


def calculate_wwind(ds: Dataset):
    return wrf.getvar(ds, 'wa', units='m s-1')


def calculate_slp(ds: Dataset):
    return wrf.getvar(ds, 'slp', units='hPa')


def calculate_cape(ds: Dataset):
    return wrf.getvar(ds, 'cape2d')


def extract_at_levels(var: xr.DataArray, pressures: xr.DataArray, levels: 'list[int]'):
    return wrf.interplevel(var, pressures, levels)


def convert_longitudes_to_0_360(ds: Dataset):
    lon = ds['XLONG'][:, :, :]
    lon = np.where(lon < 0, lon + 360, lon)
    ds['XLONG'][:, :, :] = lon
    return ds


def extract_in_domain(ds: xr.Dataset, latrange: 'tuple[float, float]', lonrange: 'tuple[float, float]'):
    return ds.sel(dict(lat=slice(*latrange), lon=slice(*lonrange)))


def construct_xr_dataset(vars: 'dict[str, xr.DataArray]', lat: npt.ArrayLike, lon: npt.ArrayLike, lev: npt.ArrayLike, attrs: dict = None):
    def specify_dimensions_name(var: xr.DataArray):
        return ('lev', 'lat', 'lon') if var.ndim == 3 else ('lat', 'lon')

    def filter_attrs(attrs: dict):
        # Projection attribute cannot be serialized by xarray.
        if 'projection' in attrs:
            del attrs['projection']
        return attrs

    return xr.Dataset(
        {name: xr.Variable(specify_dimensions_name(var), var.data, filter_attrs(var.attrs)) for name, var in vars.items()},
        coords=dict(
            lat=lat,
            lon=lon,
            lev=lev,
        ),
        attrs=attrs,
    )


def downscale(ds: xr.Dataset):
    pass


if __name__ == '__main__':
    args = parse_arguments()

    assert os.path.isfile(args.inputfile), f'Input file {args.inputfile} not found.'

    # Load dataset
    # ads = xr.load_dataset('data/nolabels_wp_ep_alllevels_ABSV_CAPE_RH_TMP_HGT_VVEL_UGRD_VGRD_100_260/12h/fnl_20080505_00_00.nc', engine='netcdf4')
    # print(ads)

    # Load input file.
    ds = Dataset(args.inputfile, 'a', diskless=True, persist=False)
    ds = convert_longitudes_to_0_360(ds)
    time = ds.variables['XTIME']
    time = num2date(time[:], time.units)
    assert len(time) == 1, 'Only works with data at a time.'
    print(time[0].strftime(TIME_FORMAT))

    pressures = calculate_pressure(ds)
    variables = [
        ['absvprs', calculate_vorticity, True],
        ['capesfc', calculate_cape, False],
        ['hgtprs', calculate_geopotential, True],
        ['rhprs', calculate_relative_humidity, True],
        ['tmpprs', calculate_temperature, True],
        # tmpsfc is missing.
        ['ugrdprs', calculate_uwind, True],
        ['vgrdprs', calculate_vwind, True],
        ['vvelprs', calculate_wwind, True],
        ['slp', calculate_slp, False],
    ]

    # Extract each variable.
    v = OrderedDict()
    for name, fn, do_extract_at_levels in variables:
        try:
            values = fn(ds)
            if do_extract_at_levels:
                values = extract_at_levels(values, pressures, PRESSURE_LEVELS)

            v[name] = values
            logging.info(f'{name} is extracted with shape {values.data.shape}.')
        except (ValueError, KeyError) as e:
            logging.warning(f'{name} cannot be extracted.')

    # Convert to xr.Dataset.
    lat_coord = ds['XLAT'][0, :, 0]
    lon_coord = ds['XLONG'][0, 0, :]
    ds = construct_xr_dataset(v, lat_coord, lon_coord, np.array(PRESSURE_LEVELS), ds.__dict__)

    # Then, extract data from the desired region.
    ds = extract_in_domain(ds, (5.0, 45.0), (100.0, 260.0))

    # Finally, save the dataset to the destination directory.
    os.makedirs(args.outputdir, exist_ok=True)
    ds.to_netcdf(
        os.path.join(args.outputdir, f'{args.prefix}_{time[0].strftime(TIME_FORMAT)}.nc'),
        mode='w',
        format='NETCDF4',
    )
