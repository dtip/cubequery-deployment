import numpy as np
import xarray as xr
import dask
from os import path

from cubequery.tasks import CubeQueryTask, Parameter, DType
from datacube_utilities import import_export
from datacube_utilities.query import (
    create_base_query,
    create_product_measurement,
    is_dataset_empty,
)


class WaterChange(CubeQueryTask):
    """
    This task uses changes in water cover to identify water change.
    """

    display_name = "Water Change"
    description = (
        "Water Change, showing changes in water cover between two time periods."
    )

    parameters = [
        Parameter("aoi", "AOI", DType.WKT, "Area of interest."),
        Parameter(
            "output_projection",
            "Output Projection",
            DType.STRING,
            "Projection to generate the output in.",
        ),
        Parameter(
            "baseline_start_date",
            "Baseline Start Date",
            DType.DATE,
            "Start date of the period to use for the baseline.",
        ),
        Parameter(
            "baseline_end_date",
            "Baseline End Date",
            DType.DATE,
            "End date of the period to use for the baseline.",
        ),
        Parameter(
            "analysis_start_date",
            "Analysis Start Date",
            DType.DATE,
            "Start date of the period to use for the analysis.",
        ),
        Parameter(
            "analysis_end_date",
            "Analysis End Date",
            DType.DATE,
            "End date of the period to use for the analysis.",
        ),
        Parameter(
            "platform_base",
            "Baseline Satellite",
            DType.STRING,
            "Satellite to use for the baseline.",
            ["SENTINEL_2", "LANDSAT_4", "LANDSAT_5", "LANDSAT_7", "LANDSAT_8"],
        ),
        Parameter(
            "platform_analysis",
            "Analysis Satellite",
            DType.STRING,
            "Satellite to use for the analysis.",
            ["SENTINEL_2", "LANDSAT_4", "LANDSAT_5", "LANDSAT_7", "LANDSAT_8"],
        ),
        Parameter(
            "res",
            "Resolution in meters",
            DType.INT,
            "Pixel resution in meters.",
            [0, 500],
        ),
        Parameter("aoi_crs", "AIO CRS", DType.STRING, "CRS of the Area of Interest."),
    ]

    CubeQueryTask.cal_significant_kwargs(parameters)

    def generate_product(
        self,
        dc,
        path_prefix,
        aoi,
        output_projection,
        baseline_start_date,
        baseline_end_date,
        analysis_start_date,
        analysis_end_date,
        platform_base,
        platform_analysis,
        res,
        aoi_crs,
        **kwargs,
    ):

        ## Create datacube query

        dask_chunks = dict(time=1, x=2000, y=2000)

        query = create_base_query(aoi, res, output_projection, aoi_crs, dask_chunks)

        all_measurements = ["green", "red", "blue", "nir", "swir1", "swir2"]
        _baseline_product, _baseline_measurement, baseline_water_product = create_product_measurement(
            platform_base, all_measurements
        )
        _analysis_product, _analysis_measurement, analysis_water_product = create_product_measurement(
            platform_analysis, all_measurements
        )

        baseline_time_period = (baseline_start_date, baseline_end_date)
        analysis_time_period = (analysis_start_date, analysis_end_date)

        ## Create dask graph

        baseline_ds = dc.load(
            time=baseline_time_period,
            platform=platform_base,
            product=baseline_water_product,
            measurements=["water_classification"],
            **query,
        )

        analysis_ds = dc.load(
            time=analysis_time_period,
            platform=platform_analysis,
            product=analysis_water_product,
            measurements=["water_classification"],
            **query,
        )

        if is_dataset_empty(baseline_ds):
            raise Exception(
                "DataCube Load returned an empty Dataset."
                + "Please check load parameters for Baseline Dataset!"
            )

        if is_dataset_empty(analysis_ds):
            raise Exception(
                "DataCube Load returned an empty Dataset."
                + "Please check load parameters for Analysis Dataset!"
            )

        wc_baseline = baseline_ds.where(baseline_ds >= 0)
        wc_analysis = analysis_ds.where(analysis_ds >= 0)

        wc_baseline_mean = wc_baseline.water_classification.mean(dim="time")
        wc_analysis_mean = wc_analysis.water_classification.mean(dim="time")

        waterpres_prob = 0.3
        T0_nd_water = np.isnan(wc_baseline_mean)
        wc_baseline_rc_int = wc_baseline_mean.where(
            (wc_baseline_mean < waterpres_prob) | (T0_nd_water == True), 1
        )  # fix > prob to water
        wc_baseline_rc = wc_baseline_rc_int.where(
            (wc_baseline_rc_int >= waterpres_prob) | (T0_nd_water == True), 0
        )  # fix < prob to no water

        T1_nd_water = np.isnan(wc_analysis_mean)
        wc_analysis_rc_int = wc_analysis_mean.where(
            (wc_analysis_mean < waterpres_prob) | (T1_nd_water == True), 1
        )  # fix > prob to water
        wc_analysis_rc = wc_analysis_rc_int.where(
            (wc_analysis_rc_int >= waterpres_prob) | (T1_nd_water == True), 0
        )  # fix < prob to no water

        # Outputs

        difference = wc_analysis_rc - wc_baseline_rc
        difference_range = wc_analysis_mean - wc_baseline_mean

        ## Compute

        difference_output, difference_range_output = dask.compute(
            difference, difference_range
        )

        ## Write files

        result = []

        file_name = path.join(path_prefix, "difference_range.tiff")
        ds = xr.DataArray.to_dataset(
            difference_range_output, dim=None, name="difference_range"
        )
        import_export.export_xarray_to_geotiff(
            ds,
            file_name,
            bands=["difference_range"],
            crs=output_projection,
            x_coord="x",
            y_coord="y",
        )
        result.append(file_name)

        file_name = path.join(path_prefix, "difference.tiff")
        ds = xr.DataArray.to_dataset(difference_output, dim=None, name="difference")
        import_export.export_xarray_to_geotiff(
            ds,
            file_name,
            bands=["difference"],
            crs=output_projection,
            x_coord="x",
            y_coord="y",
        )
        result.append(file_name)

        return result
